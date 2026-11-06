"""Races on the money paths. Row locks do nothing on SQLite, so these need PostgreSQL."""

from __future__ import annotations

import threading
from collections import Counter
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, connections
from django.utils import timezone

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry
from apps.payouts.models import PayoutAllocation, PayoutBatch, PayoutLine
from apps.payouts.services import (
    NothingToPay,
    PayoutError,
    approve_batch,
    cancel_batch,
    create_batch,
    export_batch,
    mark_line_failed,
    mark_line_paid,
    mark_line_returned,
)
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL row locks"),
]

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()
RACERS = 4


def race(*calls) -> Counter:
    """Run the calls at the same moment and count how each one ended."""
    barrier = threading.Barrier(len(calls))
    outcomes: Counter = Counter()
    lock = threading.Lock()

    def worker(call):
        try:
            barrier.wait(timeout=10)
            call()
            outcome = "ok"
        except PayoutError as exc:
            outcome = type(exc).__name__
        finally:
            connections.close_all()
        with lock:
            outcomes[outcome] += 1

    threads = [threading.Thread(target=worker, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sum(outcomes.values()) == len(calls), f"a racer crashed: {outcomes}"
    return outcomes


@pytest.fixture
def settlement(bkash, dhaka_mart, ctg_traders):
    set_bank_details(dhaka_mart, ctg_traders)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    return approve_settlement(create_settlement(bkash, YESTERDAY))


def exported_line():
    batch = export_batch(approve_batch(create_batch()))
    return batch.lines.get(merchant__code="DHAKAMART")


def test_concurrent_runs_draft_each_merchant_once(settlement):
    outcomes = race(*[create_batch] * RACERS)
    assert outcomes == Counter({"ok": 1, NothingToPay.__name__: RACERS - 1})
    assert PayoutBatch.objects.count() == 1
    assert PayoutLine.objects.count() == 2
    assert PayoutAllocation.objects.count() == 2


def test_concurrent_approvals_post_once(settlement, dhaka_mart):
    batch = create_batch()
    outcomes = race(*[lambda: approve_batch(batch)] * RACERS)
    assert outcomes == Counter({"ok": 1, "InvalidTransition": RACERS - 1})
    assert JournalEntry.objects.filter(source="payout:approve").count() == 2
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("0.00")


def test_approval_racing_cancellation_leaves_the_ledger_consistent(settlement):
    batch = create_batch()
    outcomes = race(lambda: approve_batch(batch), lambda: cancel_batch(batch))
    assert outcomes["ok"] >= 1
    batch.refresh_from_db()
    assert ledger.account("1000").balance() == (
        Decimal("-3000.00") if batch.status == PayoutBatch.Status.APPROVED else Decimal("0.00")
    )


def test_concurrent_failures_reverse_once(settlement, dhaka_mart):
    line = exported_line()
    outcomes = race(*[lambda: mark_line_failed(line, "closed account")] * RACERS)
    assert outcomes == Counter({"ok": 1, "InvalidTransition": RACERS - 1})
    assert JournalEntry.objects.filter(source="reversal").count() == 1
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("1000.00")


def test_concurrent_returns_reverse_once(settlement, dhaka_mart):
    line = mark_line_paid(exported_line())
    outcomes = race(*[lambda: mark_line_returned(line, "closed account")] * RACERS)
    assert outcomes == Counter({"ok": 1, "InvalidTransition": RACERS - 1})
    assert JournalEntry.objects.filter(source="reversal").count() == 1
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("1000.00")


def test_paid_and_failed_on_one_line_have_one_winner(settlement, dhaka_mart):
    line = exported_line()
    outcomes = race(lambda: mark_line_paid(line), lambda: mark_line_failed(line, "closed account"))
    assert outcomes == Counter({"ok": 1, "InvalidTransition": 1})
    line.refresh_from_db()
    expected = Decimal("1000.00") if line.status == PayoutLine.Status.FAILED else Decimal("0.00")
    assert ledger.merchant_payable(dhaka_mart).balance() == expected


def test_retry_runs_cannot_double_pay_a_returned_line(settlement):
    line = mark_line_paid(exported_line())
    mark_line_returned(line, "closed account")
    outcomes = race(*[create_batch] * RACERS)
    assert outcomes["ok"] == 1
    live = PayoutAllocation.objects.filter(
        merchant=line.merchant, settlement_batch=settlement, released_at__isnull=True
    )
    assert live.count() == 1
