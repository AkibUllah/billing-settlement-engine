from datetime import timedelta
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
    approve_batch,
    create_batch,
    export_batch,
    mark_batch_paid,
    mark_line_failed,
    mark_line_paid,
    mark_line_returned,
)
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()
Status = PayoutLine.Status


@pytest.fixture
def settlement(bkash, dhaka_mart, ctg_traders):
    set_bank_details(dhaka_mart, ctg_traders)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    return approve_settlement(create_settlement(bkash, YESTERDAY))


@pytest.fixture
def exported(settlement):
    return export_batch(approve_batch(create_batch()))


def line_of(batch, merchant):
    return batch.lines.get(merchant=merchant)


def payable(merchant):
    return ledger.merchant_payable(merchant).balance()


def bank():
    return ledger.account("1000").balance()


def live_allocations(merchant):
    return PayoutAllocation.objects.filter(merchant=merchant, released_at__isnull=True)


def test_paying_every_line_closes_the_batch_without_new_journals(exported, dhaka_mart, ctg_traders):
    journals = JournalEntry.objects.count()
    line = mark_line_paid(line_of(exported, dhaka_mart), "BANK-1")
    assert line.status == Status.PAID and line.bank_reference == "BANK-1" and line.paid_at
    exported.refresh_from_db()
    assert exported.status == PayoutBatch.Status.EXPORTED

    mark_line_paid(line_of(exported, ctg_traders))
    exported.refresh_from_db()
    assert exported.status == PayoutBatch.Status.PAID and exported.paid_at
    assert JournalEntry.objects.count() == journals


def test_mark_batch_paid_confirms_only_pending_lines(exported, dhaka_mart, ctg_traders):
    mark_line_failed(line_of(exported, dhaka_mart), "closed account")
    batch = mark_batch_paid(exported, "BANK-2")
    assert batch.status == PayoutBatch.Status.PAID
    assert line_of(batch, dhaka_mart).status == Status.FAILED
    assert line_of(batch, ctg_traders).status == Status.PAID
    assert line_of(batch, ctg_traders).bank_reference == "BANK-2"


def test_failed_line_reverses_only_itself(exported, dhaka_mart, ctg_traders):
    line = mark_line_failed(line_of(exported, dhaka_mart), "closed account")
    assert line.status == Status.FAILED and line.failure_reason == "closed account"
    assert line.journal.status == JournalEntry.Status.REVERSED
    assert line.reversal_journal.reverses == line.journal
    debits, credits = line.reversal_journal.totals()
    assert debits == credits == line.amount
    assert payable(dhaka_mart) == line.amount
    assert payable(ctg_traders) == Decimal("0.00")
    assert bank() == -line_of(exported, ctg_traders).amount
    assert ledger.trial_balance()["balanced"]


def test_return_after_the_batch_closed_reverses_and_leaves_it_closed(exported, dhaka_mart):
    batch = mark_batch_paid(exported)
    line = mark_line_returned(line_of(batch, dhaka_mart), "name mismatch")
    assert line.status == Status.RETURNED and line.reversed_at
    assert payable(dhaka_mart) == line.amount
    batch.refresh_from_db()
    assert batch.status == PayoutBatch.Status.PAID
    assert ledger.trial_balance()["balanced"]


@pytest.mark.parametrize("outcome", ["failed", "returned"])
def test_reversed_money_is_paid_again_exactly_once(exported, dhaka_mart, settlement, outcome):
    first = line_of(exported, dhaka_mart)
    if outcome == "failed":
        mark_line_failed(first, "closed account")
    else:
        mark_line_returned(mark_line_paid(first), "closed account")
    assert not live_allocations(dhaka_mart).exists()

    retry = create_batch()
    retry_line = retry.lines.get()
    assert retry_line.amount == first.amount
    assert [a.settlement_batch for a in live_allocations(dhaka_mart)] == [settlement]
    assert first.allocations.get().released_at is not None

    approve_batch(retry)
    assert payable(dhaka_mart) == Decimal("0.00")
    with pytest.raises(NothingToPay):
        create_batch()
    posted = dhaka_mart.payout_lines.filter(reversal_journal__isnull=True)
    assert posted.get() == retry_line  # one payment stands for the settlement, not two
    assert ledger.trial_balance()["balanced"]


def test_settlement_stays_covered_while_its_payout_stands(exported, dhaka_mart, bkash):
    mark_batch_paid(exported)
    make_txn(bkash, dhaka_mart, "400", ref="NEW", days_ago=0)
    newer = approve_settlement(create_settlement(bkash, timezone.localdate()))
    line = create_batch().lines.get()
    assert line.amount == Decimal("400.00")
    assert [a.settlement_batch for a in line.allocations.all()] == [newer]


def test_return_after_an_invoice_restores_exactly_what_was_paid(exported, dhaka_mart):
    batch = mark_batch_paid(exported)
    today = timezone.localdate()
    invoice = issue_invoice(
        generate_invoice(dhaka_mart, today - timedelta(days=30), today + timedelta(days=1))
    )
    assert payable(dhaka_mart) == -invoice.total
    line = mark_line_returned(line_of(batch, dhaka_mart), "closed account")
    assert payable(dhaka_mart) == line.amount - invoice.total
    assert create_batch().lines.get().amount == line.amount - invoice.total


def test_outcomes_are_final(exported, dhaka_mart, ctg_traders):
    failed = mark_line_failed(line_of(exported, dhaka_mart), "closed account")
    reversals = JournalEntry.objects.filter(source="reversal").count()
    for action in (
        lambda: mark_line_failed(failed, "again"),
        lambda: mark_line_paid(failed),
        lambda: mark_line_returned(failed, "again"),
    ):
        with pytest.raises(InvalidTransition):
            action()
    paid = mark_line_paid(line_of(exported, ctg_traders))
    with pytest.raises(InvalidTransition):
        mark_line_failed(paid, "changed our mind")
    with pytest.raises(InvalidTransition):
        mark_line_paid(paid)
    returned = mark_line_returned(paid, "closed account")
    with pytest.raises(InvalidTransition):
        mark_line_returned(returned, "again")
    assert JournalEntry.objects.filter(source="reversal").count() == reversals + 1


def test_lines_cannot_be_resolved_before_export(settlement, dhaka_mart):
    batch = approve_batch(create_batch())
    line = line_of(batch, dhaka_mart)
    with pytest.raises(InvalidTransition, match="batch is approved, not exported"):
        mark_line_paid(line)
    with pytest.raises(InvalidTransition, match="batch is approved, not exported"):
        mark_line_failed(line, "closed account")
    with pytest.raises(InvalidTransition, match="cannot move from pending to returned"):
        mark_line_returned(line, "closed account")
    with pytest.raises(InvalidTransition, match="cannot move from approved to paid"):
        mark_batch_paid(batch)


def test_closed_batch_cannot_be_marked_paid_again(exported):
    batch = mark_batch_paid(exported)
    with pytest.raises(InvalidTransition):
        mark_batch_paid(batch)


@pytest.mark.parametrize("action", [mark_line_failed, mark_line_returned])
def test_reversals_need_a_reason(exported, dhaka_mart, action):
    with pytest.raises(PayoutError, match="reason is required"):
        action(line_of(exported, dhaka_mart), "   ")
