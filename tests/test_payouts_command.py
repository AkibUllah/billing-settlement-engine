from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.payouts import bank_file
from apps.payouts.models import PayoutBatch
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()


@pytest.fixture
def settlement(bkash, dhaka_mart, ctg_traders):
    set_bank_details(dhaka_mart, ctg_traders)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    return approve_settlement(create_settlement(bkash, YESTERDAY))


def run(*args):
    out = StringIO()
    call_command("run_payouts", *args, stdout=out)
    return out.getvalue()


def test_drafts_by_default(settlement):
    output = run("--date", "2026-11-08")
    batch = PayoutBatch.objects.get()
    assert batch.status == "draft" and batch.value_date.isoformat() == "2026-11-08"
    assert batch.reference in output and "৳3,000.00" in output


def test_reports_skipped_merchants(settlement):
    output = run("--minimum", "1500", "--approve")
    assert PayoutBatch.objects.get().status == "approved"
    assert "skipped DHAKAMART (1000.00): below the minimum of 1500.00" in output


def test_merchant_filter_uppercases_codes(settlement):
    run("--merchant", "dhakamart")
    assert PayoutBatch.objects.get().lines.get().merchant.code == "DHAKAMART"


def test_export_dir_writes_a_valid_file(settlement, tmp_path):
    output = run("--export-dir", str(tmp_path))
    batch = PayoutBatch.objects.get()
    assert batch.status == "exported"
    path = tmp_path / f"{batch.reference}.dcn1.csv"
    assert str(path) in output and batch.bank_file_sha256 in output
    assert path.read_bytes() == batch.bank_file.encode()
    assert len(bank_file.parse(path.read_text()).entries) == 2


def test_nothing_to_pay_is_a_warning_not_a_failure(db):
    assert "no merchant is eligible" in run()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--merchant", "NOPE"), "unknown merchant code"),
        (("--date", "soon"), "bad --date"),
        (("--minimum", "lots"), "bad --minimum"),
        (("--minimum", "-5"), "cannot be negative"),
        (("--export-dir", "/nonexistent/dir"), "is not a directory"),
    ],
)
def test_bad_arguments_are_rejected(settlement, args, message):
    with pytest.raises(CommandError, match=message):
        run(*args)
    assert not PayoutBatch.objects.exists()
