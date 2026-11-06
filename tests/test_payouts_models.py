from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payouts.models import PayoutAllocation, PayoutBatch, PayoutLine
from apps.settlement.models import SettlementBatch

pytestmark = pytest.mark.django_db

Batch = PayoutBatch.Status
Line = PayoutLine.Status


def make_line(batch, merchant, sequence=1, **fields):
    defaults = {
        "reference": f"{batch.reference}-{sequence}",
        "amount": Decimal("100.00"),
        "routing_number": "990000011",
        "account_number": "7001000099",
        "account_name": "Dhaka Mart Ltd.",
    }
    return PayoutLine.objects.create(
        batch=batch, merchant=merchant, sequence=sequence, **{**defaults, **fields}
    )


@pytest.fixture
def batch(db):
    return PayoutBatch.objects.create(reference="PO-TEST-1", value_date=date(2026, 11, 8))


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        (Batch.DRAFT, {Batch.APPROVED, Batch.CANCELLED}),
        (Batch.APPROVED, {Batch.EXPORTED, Batch.CANCELLED}),
        (Batch.EXPORTED, {Batch.PAID}),
        (Batch.PAID, set()),
        (Batch.CANCELLED, set()),
    ],
)
def test_batch_transitions_are_a_whitelist(source, allowed):
    batch = PayoutBatch(status=source)
    assert {target for target in Batch if batch.can_transition(target)} == allowed


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        (Line.PENDING, {Line.PAID, Line.FAILED, Line.CANCELLED}),
        (Line.PAID, {Line.RETURNED}),
        (Line.FAILED, set()),
        (Line.RETURNED, set()),
        (Line.CANCELLED, set()),
    ],
)
def test_line_transitions_are_a_whitelist(source, allowed):
    line = PayoutLine(status=source)
    assert {target for target in Line if line.can_transition(target)} == allowed


def test_database_rejects_non_positive_amounts(batch, dhaka_mart):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(batch, dhaka_mart, amount=Decimal("0.00"))


def test_merchant_can_hold_only_one_unposted_line(batch, dhaka_mart):
    make_line(batch, dhaka_mart)
    other = PayoutBatch.objects.create(reference="PO-TEST-2", value_date=date(2026, 11, 8))
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(other, dhaka_mart, reference="PO-TEST-2-1")


def test_settlement_share_has_only_one_live_allocation(batch, dhaka_mart, bkash):
    line = make_line(batch, dhaka_mart)
    settlement = SettlementBatch.objects.create(
        reference="SET-1", partner=bkash, cutoff_date=date(2026, 11, 1)
    )
    fields = {
        "line": line,
        "settlement_batch": settlement,
        "merchant": dhaka_mart,
        "credited_amount": Decimal("100.00"),
    }
    first = PayoutAllocation.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        PayoutAllocation.objects.create(**fields)
    first.released_at = timezone.now()
    first.save()
    PayoutAllocation.objects.create(**fields)  # released money may be paid again
    assert PayoutAllocation.objects.filter(released_at__isnull=True).count() == 1
