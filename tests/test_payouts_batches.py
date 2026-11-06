from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.services import generate_invoice, issue_invoice
from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry
from apps.payouts.models import PayoutAllocation, PayoutBatch, PayoutLine
from apps.payouts.services import (
    InvalidTransition,
    NothingToPay,
    PayoutError,
    StalePayout,
    approve_batch,
    cancel_batch,
    create_batch,
)
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()


def settle(partner, cutoff=YESTERDAY):
    return approve_settlement(create_settlement(partner, cutoff))


@pytest.fixture
def funded(bkash, dhaka_mart, ctg_traders):
    set_bank_details(dhaka_mart, ctg_traders)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    make_txn(bkash, dhaka_mart, "300", ref="C", type="refund")
    return settle(bkash)


def test_lines_equal_payable_balances_at_creation(funded, dhaka_mart, ctg_traders):
    batch = create_batch(date(2026, 11, 8))
    lines = {line.merchant.code: line for line in batch.lines.select_related("merchant")}
    assert lines["DHAKAMART"].amount == ledger.merchant_payable(dhaka_mart).balance()
    assert lines["DHAKAMART"].amount == Decimal("700.00")
    assert lines["CTGTRADERS"].amount == ledger.merchant_payable(ctg_traders).balance()
    assert batch.line_count == 2 and batch.total_amount == Decimal("2700.00")
    assert batch.status == PayoutBatch.Status.DRAFT
    assert batch.reference == f"PO-20261108-{batch.pk:06d}"
    assert [line.sequence for line in batch.lines.order_by("merchant__code")] == [1, 2]
    assert lines["CTGTRADERS"].reference == f"{batch.reference}-0001"


def test_invoiced_fees_are_netted_out_of_the_payout(funded, dhaka_mart):
    today = timezone.localdate()
    invoice = issue_invoice(
        generate_invoice(dhaka_mart, today - timedelta(days=30), today + timedelta(days=1))
    )
    assert invoice.total > 0
    line = create_batch().lines.get(merchant=dhaka_mart)
    assert line.amount == Decimal("700.00") - invoice.total


def test_line_snapshots_bank_details(funded, dhaka_mart):
    line = create_batch().lines.get(merchant=dhaka_mart)
    dhaka_mart.bank_account_number = "9999999999"
    dhaka_mart.save()
    line.refresh_from_db()
    assert line.account_number != "9999999999"


def test_lines_record_which_settlements_they_cover(funded, dhaka_mart):
    line = create_batch().lines.get(merchant=dhaka_mart)
    allocation = line.allocations.get()
    assert allocation.settlement_batch == funded
    assert allocation.credited_amount == Decimal("700.00")


def test_minimum_leaves_small_balances_and_says_why(funded):
    batch = create_batch(minimum_amount=Decimal("1000"))
    assert [line.merchant.code for line in batch.lines.all()] == ["CTGTRADERS"]
    assert batch.skipped == [
        {"merchant": "DHAKAMART", "balance": "700.00", "reason": "below the minimum of 1000.00"}
    ]


def test_balance_equal_to_the_minimum_is_paid(funded):
    assert create_batch(minimum_amount=Decimal("700.00")).line_count == 2


def test_merchant_without_bank_details_is_skipped(funded, dhaka_mart):
    dhaka_mart.bank_routing_number = ""
    dhaka_mart.save()
    batch = create_batch()
    assert [line.merchant.code for line in batch.lines.all()] == ["CTGTRADERS"]
    assert "bank_routing_number is missing" in batch.skipped[0]["reason"]


def test_inactive_merchants_and_merchant_filter(funded, dhaka_mart, ctg_traders):
    assert create_batch(merchants=[dhaka_mart]).line_count == 1
    ctg_traders.is_active = False
    ctg_traders.save()
    with pytest.raises(NothingToPay):
        create_batch()


def test_merchant_cannot_be_in_two_unapproved_batches(funded, dhaka_mart):
    create_batch(merchants=[dhaka_mart])
    second = create_batch()
    assert [line.merchant.code for line in second.lines.all()] == ["CTGTRADERS"]
    assert second.skipped[0]["reason"] == "already in an unapproved payout batch"
    assert PayoutLine.objects.filter(merchant=dhaka_mart).count() == 1


def test_nothing_to_pay_raises_and_creates_no_batch(funded, dhaka_mart, ctg_traders):
    create_batch()
    with pytest.raises(NothingToPay, match="already in an unapproved payout batch"):
        create_batch()
    assert PayoutBatch.objects.count() == 1


def test_no_balances_at_all(db):
    with pytest.raises(NothingToPay, match="^no merchant is eligible for payout$"):
        create_batch()


def test_negative_minimum_rejected(db):
    with pytest.raises(PayoutError, match="cannot be negative"):
        create_batch(minimum_amount=Decimal("-1"))


def bank_balance():
    return ledger.account("1000").balance()


def test_approval_posts_one_balanced_journal_per_line(funded, dhaka_mart, ctg_traders, api):
    batch = approve_batch(create_batch(), user=api.user)
    assert batch.status == PayoutBatch.Status.APPROVED
    assert batch.approved_by == api.user and batch.approved_at
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("0.00")
    assert ledger.merchant_payable(ctg_traders).balance() == Decimal("0.00")
    assert bank_balance() == Decimal("-2700.00")
    for line in batch.lines.select_related("journal", "merchant"):
        debits, credits = line.journal.totals()
        assert debits == credits == line.amount
        postings = {p.direction: p.account.code for p in line.journal.postings.all()}
        assert postings == {"debit": f"2000-{line.merchant.code}", "credit": "1000"}
    assert ledger.trial_balance()["balanced"]


def test_second_run_after_approval_pays_nothing_more(funded):
    approve_batch(create_batch())
    with pytest.raises(NothingToPay):
        create_batch()


def test_approving_twice_is_rejected_and_posts_nothing(funded):
    batch = approve_batch(create_batch())
    journals = JournalEntry.objects.count()
    with pytest.raises(InvalidTransition, match="cannot move from approved to approved"):
        approve_batch(batch)
    assert JournalEntry.objects.count() == journals


def test_new_settlement_is_paid_while_the_previous_line_is_in_flight(funded, bkash, dhaka_mart):
    first = approve_batch(create_batch())
    make_txn(bkash, dhaka_mart, "500", ref="LATER", days_ago=0)
    second_settlement = settle(bkash, timezone.localdate())

    line = create_batch().lines.get()
    assert first.lines.filter(merchant=dhaka_mart).exists()
    assert line.amount == Decimal("500.00")
    assert [a.settlement_batch for a in line.allocations.all()] == [second_settlement]


def test_invoice_after_drafting_makes_approval_refuse_the_stale_amount(funded, dhaka_mart):
    batch = create_batch()
    today = timezone.localdate()
    issue_invoice(
        generate_invoice(dhaka_mart, today - timedelta(days=30), today + timedelta(days=1))
    )
    with pytest.raises(StalePayout, match="DHAKAMART payable is .* below the line amount 700.00"):
        approve_batch(batch)
    batch.refresh_from_db()
    assert batch.status == PayoutBatch.Status.DRAFT
    assert not JournalEntry.objects.filter(source="payout:approve").exists()


def test_credits_after_drafting_stay_for_the_next_batch(funded, bkash, dhaka_mart):
    batch = create_batch(merchants=[dhaka_mart])
    make_txn(bkash, dhaka_mart, "500", ref="LATER", days_ago=0)
    settle(bkash, timezone.localdate())
    approve_batch(batch)
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("500.00")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"is_active": False}, "DHAKAMART is inactive"),
        ({"bank_account_number": "7999999999"}, "bank details changed"),
    ],
)
def test_approval_refuses_merchants_changed_since_drafting(funded, dhaka_mart, change, message):
    batch = create_batch(merchants=[dhaka_mart])
    for field, value in change.items():
        setattr(dhaka_mart, field, value)
    dhaka_mart.save()
    with pytest.raises(StalePayout, match=message):
        approve_batch(batch)


def test_cancelling_a_draft_frees_merchants_and_settlements(funded, dhaka_mart):
    batch = cancel_batch(create_batch(), "wrong value date")
    assert batch.status == PayoutBatch.Status.CANCELLED and batch.cancelled_at
    assert set(batch.lines.values_list("status", flat=True)) == {PayoutLine.Status.CANCELLED}
    assert batch.lines.first().failure_reason == "wrong value date"
    assert not PayoutAllocation.objects.filter(released_at__isnull=True).exists()
    assert not JournalEntry.objects.filter(source="payout:approve").exists()

    again = create_batch()
    assert again.line_count == 2
    assert PayoutAllocation.objects.filter(released_at__isnull=True).count() == 2


def test_cancelling_an_approved_batch_reverses_every_journal(funded, dhaka_mart, ctg_traders):
    batch = approve_batch(create_batch())
    batch = cancel_batch(batch)
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("700.00")
    assert ledger.merchant_payable(ctg_traders).balance() == Decimal("2000.00")
    assert bank_balance() == Decimal("0.00")
    for line in batch.lines.all():
        assert line.journal.status == JournalEntry.Status.REVERSED
        assert line.reversal_journal.reverses == line.journal
    assert ledger.trial_balance()["balanced"]
    assert create_batch().total_amount == Decimal("2700.00")


def test_cancelling_twice_is_rejected(funded):
    batch = cancel_batch(create_batch())
    with pytest.raises(InvalidTransition):
        cancel_batch(batch)
    with pytest.raises(InvalidTransition):
        approve_batch(batch)
