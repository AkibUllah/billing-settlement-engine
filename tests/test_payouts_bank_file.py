from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.payouts import bank_file
from apps.payouts.bank_file import BankFileError, Entry
from apps.payouts.models import PayoutBatch
from apps.payouts.services import (
    InvalidTransition,
    PayoutError,
    approve_batch,
    bank_file_content,
    bank_file_name,
    cancel_batch,
    create_batch,
    export_batch,
    render_bank_file,
)
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()

ENTRIES = [
    Entry(1, "990000001", "700100000001", "Chittagong Traders & Co.", Decimal("2000.00"), "PO-1-1"),
    Entry(2, "990000002", "700100000002", "Dhaka Mart Ltd.", Decimal("700.50"), "PO-1-2"),
]
GOLDEN = """\
H,DCN1,DEMOPLATFORM,PO-1,20261108,BDT
D,1,990000001,700100000001,Chittagong Traders & Co.,2000.00,PO-1-1
D,2,990000002,700100000002,Dhaka Mart Ltd.,700.50,PO-1-2
T,2,2700.50,1980000003
"""


def rendered(entries=ENTRIES):
    return bank_file.render(
        originator="DEMOPLATFORM",
        batch_reference="PO-1",
        value_date=date(2026, 11, 8),
        currency="BDT",
        entries=entries,
    )


def test_render_matches_the_documented_layout_byte_for_byte():
    assert rendered() == GOLDEN


def test_render_ignores_input_order():
    assert rendered(list(reversed(ENTRIES))) == GOLDEN


def test_parse_round_trips():
    parsed = bank_file.parse(GOLDEN)
    assert parsed.originator == "DEMOPLATFORM" and parsed.batch_reference == "PO-1"
    assert parsed.value_date == date(2026, 11, 8) and parsed.currency == "BDT"
    assert parsed.entries == tuple(ENTRIES) and parsed.total == Decimal("2700.50")


def test_routing_hash_wraps_at_ten_digits():
    assert bank_file.routing_hash(["999999999"] * 11) == "0999999989"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("H,DCN1,", "H,DCN9,", "unsupported format"),
        ("DEMOPLATFORM", "demo platform", "bad originator"),
        ("20261108", "20261332", "bad value date"),
        ("2000.00,PO-1-1", "2100.00,PO-1-1", "trailer total"),
        ("2000.00,PO-1-1", "0.00,PO-1-1", "bad amount"),
        ("2000.00,PO-1-1", "-5.00,PO-1-1", "bad amount"),
        ("2000.00,PO-1-1", "2000.5,PO-1-1", "bad amount"),
        ("D,2,990000002", "D,3,990000002", "sequence must run"),
        ("D,2,990000002", "D,2,990000003", "routing hash"),
        ("D,2,990000002", "D,2,9900", "9 digits"),
        ("700100000002", "12", "6-20 digits"),
        ("Dhaka Mart Ltd.", "=cmd()", "Account name"),
        ("PO-1-2", "PO-1-1", "must be unique"),
        ("T,2,", "T,3,", "trailer count"),
        ("T,2,", "X,2,", "unknown record type"),
        (",PO-1-2\n", ",PO-1-2,extra\n", "expected 7 fields"),
    ],
)
def test_parse_rejects_tampering(old, new, message):
    assert old in GOLDEN
    with pytest.raises(BankFileError, match=message):
        bank_file.parse(GOLDEN.replace(old, new))


@pytest.mark.parametrize(
    "content",
    [
        "",
        "H,DCN1,X,PO-1,20261108,BDT\nT,0,0.00,0000000000\n",
        GOLDEN.split("\n", 1)[1],
        GOLDEN + GOLDEN.splitlines()[-1] + "\n",
    ],
)
def test_parse_rejects_bad_structure(content):
    with pytest.raises(BankFileError):
        bank_file.parse(content)


@pytest.fixture
def approved(bkash, dhaka_mart, ctg_traders):
    set_bank_details(ctg_traders, dhaka_mart)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    approve_settlement(create_settlement(bkash, YESTERDAY))
    return approve_batch(create_batch(date(2026, 11, 8)))


def test_export_stores_a_valid_file_for_the_batch(approved, settings):
    settings.PAYOUT_ORIGINATOR_ID = "DEMOPLATFORM"
    batch = export_batch(approved)
    assert batch.status == PayoutBatch.Status.EXPORTED and batch.exported_at
    r = batch.reference
    assert bank_file_content(batch) == (
        f"H,DCN1,DEMOPLATFORM,{r},20261108,BDT\n"
        f"D,1,990000001,700100000001,Chittagong Traders,2000.00,{r}-0001\n"
        f"D,2,990000002,700100000002,Dhaka Mart Ltd.,1000.00,{r}-0002\n"
        "T,2,3000.00,1980000003\n"
    )
    assert batch.bank_file_sha256 == bank_file.sha256(batch.bank_file)
    assert bank_file_name(batch) == f"{r}.dcn1.csv"


def test_the_exported_file_does_not_change_when_merchants_or_settings_do(
    approved, dhaka_mart, settings
):
    batch = export_batch(approved)
    original = bank_file_content(batch)
    dhaka_mart.bank_account_number = "7999999999"
    dhaka_mart.save()
    settings.PAYOUT_ORIGINATOR_ID = "OTHER"
    batch.refresh_from_db()
    assert bank_file_content(batch) == original


def test_render_is_deterministic_across_exports_of_equal_batches(approved):
    assert render_bank_file(approved) == render_bank_file(PayoutBatch.objects.get(pk=approved.pk))


def test_export_requires_an_approved_batch(bkash, dhaka_mart):
    set_bank_details(dhaka_mart)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    approve_settlement(create_settlement(bkash, YESTERDAY))
    draft = create_batch()
    with pytest.raises(InvalidTransition, match="cannot move from draft to exported"):
        export_batch(draft)
    with pytest.raises(InvalidTransition, match="has not been exported"):
        bank_file_content(draft)


def test_export_is_one_way(approved):
    batch = export_batch(approved)
    with pytest.raises(InvalidTransition):
        export_batch(batch)
    with pytest.raises(InvalidTransition):
        cancel_batch(batch)
    with pytest.raises(InvalidTransition):
        approve_batch(batch)


def test_export_refuses_a_file_that_fails_validation(approved, settings):
    settings.PAYOUT_ORIGINATOR_ID = "not valid"
    with pytest.raises(PayoutError, match="generated file is invalid: bad originator"):
        export_batch(approved)
    approved.refresh_from_db()
    assert approved.status == PayoutBatch.Status.APPROVED and not approved.bank_file
