"""Payout batches: what we owe merchants, the approval workflow and the bank file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.accounts.models import Merchant
from apps.common import ZERO, money
from apps.ledger import services as ledger
from apps.ledger.models import Posting
from apps.payouts.models import PayoutAllocation, PayoutBatch, PayoutLine
from apps.settlement.models import SettlementBatch


class PayoutError(Exception):
    pass


class InvalidTransition(PayoutError):
    pass


class NothingToPay(PayoutError):
    pass


class StalePayout(PayoutError):
    """The line no longer matches the merchant: cancel the batch and draft a new one."""


@dataclass(frozen=True)
class Candidate:
    merchant: Merchant
    amount: Decimal


def _uncovered_settlements(merchant: Merchant) -> list[tuple[SettlementBatch, Decimal]]:
    """Settlement credits to this merchant's payable that no live payout line covers yet."""
    credits = {
        posting.journal_id: posting.amount
        for posting in Posting.objects.filter(
            account=ledger.merchant_payable(merchant),
            direction=Posting.Direction.CREDIT,
            journal__source="settlement:approve",
        )
    }
    covered = PayoutAllocation.objects.filter(
        merchant=merchant, released_at__isnull=True
    ).values_list("settlement_batch_id", flat=True)
    settlements = (
        SettlementBatch.objects.filter(approval_journal_id__in=credits)
        .exclude(pk__in=covered)
        .order_by("cutoff_date", "pk")
    )
    return [(s, credits[s.approval_journal_id]) for s in settlements]


def _select_candidates(
    merchants: list[Merchant], minimum: Decimal
) -> tuple[list[Candidate], list[dict]]:
    unposted = set(
        PayoutLine.objects.filter(
            merchant__in=merchants, status=PayoutLine.Status.PENDING, journal__isnull=True
        ).values_list("merchant_id", flat=True)
    )
    candidates: list[Candidate] = []
    skipped: list[dict] = []

    def skip(merchant: Merchant, balance: Decimal, reason: str) -> None:
        skipped.append({"merchant": merchant.code, "balance": str(balance), "reason": reason})

    for merchant in merchants:
        balance = ledger.merchant_payable(merchant).balance()
        if balance <= ZERO:
            continue
        if merchant.pk in unposted:
            skip(merchant, balance, "already in an unapproved payout batch")
        elif balance < minimum:
            skip(merchant, balance, f"below the minimum of {minimum}")
        elif errors := merchant.bank_detail_errors():
            skip(merchant, balance, "; ".join(errors))
        else:
            candidates.append(Candidate(merchant, balance))
    return candidates, skipped


@db_transaction.atomic
def create_batch(
    value_date: date | None = None,
    *,
    minimum_amount: Decimal = ZERO,
    merchants: list[Merchant] | None = None,
    user=None,
) -> PayoutBatch:
    """Draft a batch paying each eligible merchant their full payable balance.

    Merchant rows stay locked until commit, so two concurrent runs cannot read the same balances.
    Raises NothingToPay when no merchant qualifies.
    """
    minimum = money(minimum_amount)
    if minimum < ZERO:
        raise PayoutError("minimum_amount cannot be negative")
    locked = Merchant.objects.select_for_update().filter(is_active=True).order_by("code")
    if merchants is not None:
        locked = locked.filter(pk__in=[m.pk for m in merchants])
    candidates, skipped = _select_candidates(list(locked), minimum)
    if not candidates:
        detail = "; ".join(f"{s['merchant']}: {s['reason']}" for s in skipped)
        raise NothingToPay(
            "no merchant is eligible for payout" + (f" ({detail})" if detail else "")
        )

    value_date = value_date or timezone.localdate()
    batch = PayoutBatch.objects.create(
        # The primary key makes the reference unique without a race on a per-day counter.
        reference=f"PENDING-{uuid4().hex}",
        value_date=value_date,
        minimum_amount=minimum,
        line_count=len(candidates),
        total_amount=money(sum((c.amount for c in candidates), ZERO)),
        skipped=skipped,
        created_by=user if getattr(user, "pk", None) else None,
    )
    batch.reference = f"PO-{value_date:%Y%m%d}-{batch.pk:06d}"
    batch.save(update_fields=["reference"])

    for sequence, candidate in enumerate(candidates, start=1):
        merchant = candidate.merchant
        line = PayoutLine.objects.create(
            batch=batch,
            merchant=merchant,
            sequence=sequence,
            reference=f"{batch.reference}-{sequence:04d}",
            amount=candidate.amount,
            routing_number=merchant.bank_routing_number,
            account_number=merchant.bank_account_number,
            account_name=merchant.bank_account_name,
        )
        PayoutAllocation.objects.bulk_create(
            PayoutAllocation(
                line=line, settlement_batch=settlement, merchant=merchant, credited_amount=credited
            )
            for settlement, credited in _uncovered_settlements(merchant)
        )
    return batch


def _lock_batch(batch: PayoutBatch) -> PayoutBatch:
    """Re-read under a row lock, so concurrent workflow calls run one at a time on fresh state."""
    return PayoutBatch.objects.select_for_update().get(pk=batch.pk)


def _transition(record: PayoutBatch | PayoutLine, target: str) -> None:
    if not record.can_transition(target):
        raise InvalidTransition(f"{record.reference}: cannot move from {record.status} to {target}")
    record.status = target


def _staleness(line: PayoutLine, merchant: Merchant) -> str | None:
    """Why this line must not be paid any more, if it must not."""
    if not merchant.is_active:
        return f"{merchant.code} is inactive"
    if (line.routing_number, line.account_number, line.account_name) != (
        merchant.bank_routing_number,
        merchant.bank_account_number,
        merchant.bank_account_name,
    ):
        return f"{merchant.code} bank details changed after the batch was drafted"
    balance = ledger.merchant_payable(merchant).balance()
    if line.amount > balance:
        return f"{merchant.code} payable is {balance}, below the line amount {line.amount}"
    return None


def _post_line(line: PayoutLine, merchant: Merchant) -> None:
    line.journal = ledger.post_journal(
        reference=f"PAYOUT:{line.reference}",
        description=f"Payout {line.reference} to {merchant.name}",
        source="payout:approve",
        lines=[
            ledger.debit(ledger.merchant_payable(merchant), line.amount, "payout"),
            ledger.credit(ledger.account("1000"), line.amount, line.reference),
        ],
    )
    line.save(update_fields=["journal"])


def _reverse_line(line: PayoutLine, reason: str) -> None:
    """Put the money back on the payable and free the settlements for a later batch."""
    now = timezone.now()
    line.reversal_journal = ledger.reverse_journal(
        line.journal,
        reference=f"{line.journal.reference}:REV",
        description=f"Payout {line.reference} {line.status}: {reason}"[:300],
    )
    line.reversed_at = now
    line.allocations.update(released_at=now)


@db_transaction.atomic
def approve_batch(batch: PayoutBatch, user=None) -> PayoutBatch:
    """DRAFT -> APPROVED. Posts Dr merchant payable / Cr bank for each line, or nothing at all."""
    batch = _lock_batch(batch)
    _transition(batch, PayoutBatch.Status.APPROVED)
    lines = list(batch.lines.order_by("sequence"))
    merchants = {
        m.pk: m
        for m in Merchant.objects.select_for_update()
        .filter(pk__in=[line.merchant_id for line in lines])
        .order_by("code")
    }
    stale = [_staleness(line, merchants[line.merchant_id]) for line in lines]
    if problems := [problem for problem in stale if problem]:
        raise StalePayout(f"{batch.reference} is stale: " + "; ".join(problems))
    for line in lines:
        _post_line(line, merchants[line.merchant_id])
    batch.approved_by = user if getattr(user, "pk", None) else None
    batch.approved_at = timezone.now()
    batch.save(update_fields=["status", "approved_by", "approved_at"])
    return batch


@db_transaction.atomic
def cancel_batch(batch: PayoutBatch, reason: str = "cancelled") -> PayoutBatch:
    """DRAFT or APPROVED -> CANCELLED. Once a file has left, resolve lines one by one instead."""
    batch = _lock_batch(batch)
    _transition(batch, PayoutBatch.Status.CANCELLED)
    now = timezone.now()
    for line in batch.lines.select_related("journal").order_by("sequence"):
        _transition(line, PayoutLine.Status.CANCELLED)
        if line.journal_id:
            _reverse_line(line, reason)
        else:
            line.allocations.update(released_at=now)
        line.failure_reason = reason[:200]
        line.save(
            update_fields=["status", "reversal_journal", "reversed_at", "failure_reason"],
        )
    batch.cancelled_at = now
    batch.save(update_fields=["status", "cancelled_at"])
    return batch
