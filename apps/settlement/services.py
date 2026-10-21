"""Batch creation, approval workflow with ledger postings, CSV export and reconciliation."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import Merchant, Partner
from apps.billing.models import Transaction
from apps.common import ZERO, money
from apps.ledger import services as ledger
from apps.settlement.models import ReconciliationReport, SettlementBatch


class SettlementError(Exception):
    pass


class InvalidTransition(SettlementError):
    pass


def _end_of_day(cutoff: date) -> datetime:
    return timezone.make_aware(datetime.combine(cutoff, time.max), timezone.get_current_timezone())


def batch_reference(partner: Partner, cutoff: date) -> str:
    return f"SET-{partner.code.upper()}-{cutoff:%Y%m%d}"


@db_transaction.atomic
def create_batch(partner: Partner, cutoff: date) -> SettlementBatch | None:
    """Net all unsettled transactions for the partner up to the cutoff into one batch.

    net = gross payments - refunds - partner commission - VAT on commission.
    Returns None when there is nothing to settle. Re-running for the same partner/cutoff raises.
    """
    if SettlementBatch.objects.filter(partner=partner, cutoff_date=cutoff).exists():
        raise SettlementError(f"batch for {partner.code} @ {cutoff} already exists")

    txns = Transaction.objects.select_for_update().filter(
        partner=partner,
        status=Transaction.Status.UNSETTLED,
        settlement_batch__isnull=True,
        occurred_at__lte=_end_of_day(cutoff),
    )
    if not txns.exists():
        return None

    payments = txns.filter(type=Transaction.Type.PAYMENT).aggregate(
        gross=Sum("amount"), fee=Sum("partner_fee"), vat=Sum("partner_vat")
    )
    refunds = txns.filter(type=Transaction.Type.REFUND).aggregate(total=Sum("amount"))
    gross = money(payments["gross"] or ZERO)
    fee = money(payments["fee"] or ZERO)
    vat = money(payments["vat"] or ZERO)
    refund = money(refunds["total"] or ZERO)

    batch = SettlementBatch.objects.create(
        reference=batch_reference(partner, cutoff),
        partner=partner,
        cutoff_date=cutoff,
        currency=partner.currency,
        transaction_count=txns.count(),
        gross_amount=gross,
        refund_amount=refund,
        fee_amount=fee,
        vat_amount=vat,
        net_amount=money(gross - refund - fee - vat),
    )
    txns.update(settlement_batch=batch, status=Transaction.Status.SETTLED)
    return batch


def run_settlement(cutoff: date, partners: list[Partner] | None = None) -> list[SettlementBatch]:
    partners = partners or list(Partner.objects.filter(is_active=True))
    created: list[SettlementBatch] = []
    for partner in partners:
        if SettlementBatch.objects.filter(partner=partner, cutoff_date=cutoff).exists():
            continue
        batch = create_batch(partner, cutoff)
        if batch is not None:
            created.append(batch)
    return created


def _transition(batch: SettlementBatch, target: str) -> None:
    if not batch.can_transition(target):
        raise InvalidTransition(f"{batch.reference}: cannot move from {batch.status} to {target}")
    batch.status = target


@db_transaction.atomic
def approve_batch(batch: SettlementBatch, user=None) -> SettlementBatch:
    """PENDING -> APPROVED. Recognises the receivable from the partner and what we owe merchants."""
    _transition(batch, SettlementBatch.Status.APPROVED)
    lines = [
        ledger.debit(
            ledger.partner_receivable(batch.partner), batch.net_amount, "net settlement due"
        ),
        ledger.debit(ledger.account("5000"), batch.fee_amount, "partner commission"),
        ledger.debit(ledger.account("1300"), batch.vat_amount, "VAT on commission"),
    ]
    # Credit each merchant's payable with their share of collections (gross - refunds).
    per_merchant = (
        batch.transactions.values("merchant")
        .annotate(
            gross=Sum("amount", filter=Q(type="payment")),
            refunds=Sum("amount", filter=Q(type="refund")),
        )
        .order_by("merchant")
    )
    for row in per_merchant:
        merchant = Merchant.objects.get(pk=row["merchant"])
        net = money((row["gross"] or ZERO) - (row["refunds"] or ZERO))
        if net != ZERO:
            lines.append(ledger.credit(ledger.merchant_payable(merchant), net, "collections"))
    batch.approval_journal = ledger.post_journal(
        reference=f"{batch.reference}:APPROVE",
        description=f"Settlement {batch.reference} approved",
        source="settlement:approve",
        lines=lines,
    )
    batch.approved_by = user if getattr(user, "pk", None) else None
    batch.approved_at = timezone.now()
    batch.save(update_fields=["status", "approval_journal", "approved_by", "approved_at"])
    return batch


@db_transaction.atomic
def mark_paid(batch: SettlementBatch, payment_reference: str = "") -> SettlementBatch:
    """APPROVED -> PAID. Partner remitted the net amount to our bank."""
    _transition(batch, SettlementBatch.Status.PAID)
    batch.payment_journal = ledger.post_journal(
        reference=f"{batch.reference}:PAID",
        description=f"Settlement {batch.reference} received from {batch.partner.name}",
        source="settlement:paid",
        lines=[
            ledger.debit(
                ledger.account("1000"), batch.net_amount, payment_reference or "partner remittance"
            ),
            ledger.credit(ledger.partner_receivable(batch.partner), batch.net_amount, "settled"),
        ],
    )
    batch.paid_at = timezone.now()
    batch.payment_reference = payment_reference
    batch.save(update_fields=["status", "payment_journal", "paid_at", "payment_reference"])
    return batch


def export_csv(batch: SettlementBatch) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "batch",
            "external_ref",
            "type",
            "merchant",
            "amount",
            "partner_fee",
            "partner_vat",
            "occurred_at",
        ]
    )
    for t in batch.transactions.select_related("merchant").order_by("occurred_at"):
        writer.writerow(
            [
                batch.reference,
                t.external_ref,
                t.type,
                t.merchant.code,
                t.amount,
                t.partner_fee,
                t.partner_vat,
                t.occurred_at.isoformat(),
            ]
        )
    writer.writerow(
        [batch.reference, "NET", "", "", batch.net_amount, batch.fee_amount, batch.vat_amount, ""]
    )
    return buffer.getvalue()


def parse_partner_report(content: str | bytes) -> list[dict]:
    """Partner report CSV with columns external_ref, type, amount (header required)."""
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text))
    required = {"external_ref", "type", "amount"}
    if not reader.fieldnames or not required <= {f.strip().lower() for f in reader.fieldnames}:
        raise SettlementError("report needs columns: external_ref, type, amount")
    rows = []
    for index, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not row.get("external_ref"):
            continue
        try:
            amount = money(row["amount"])
        except (InvalidOperation, ValueError) as exc:
            raise SettlementError(f"line {index}: bad amount {row['amount']!r}") from exc
        rows.append(
            {
                "external_ref": row["external_ref"],
                "type": row["type"].lower() or "payment",
                "amount": amount,
            }
        )
    return rows


@db_transaction.atomic
def reconcile(
    batch: SettlementBatch, content: str | bytes, source_name: str = ""
) -> ReconciliationReport:
    """Diff the partner's report against the batch's transactions."""
    if batch.status == SettlementBatch.Status.PAID:
        raise SettlementError("paid batches are closed for reconciliation")
    rows = parse_partner_report(content)
    report_by_ref = {r["external_ref"]: r for r in rows}
    ledger_by_ref = {t.external_ref: t for t in batch.transactions.all()}

    matched = 0
    mismatches: list[dict] = []
    missing_in_partner: list[str] = []
    for ref, txn in ledger_by_ref.items():
        row = report_by_ref.get(ref)
        if row is None:
            missing_in_partner.append(ref)
            continue
        if row["amount"] != txn.amount or row["type"] != txn.type:
            mismatches.append(
                {
                    "external_ref": ref,
                    "ledger_amount": str(txn.amount),
                    "partner_amount": str(row["amount"]),
                    "ledger_type": txn.type,
                    "partner_type": row["type"],
                }
            )
        else:
            matched += 1
    missing_in_ledger = sorted(set(report_by_ref) - set(ledger_by_ref))

    partner_total = money(
        sum((r["amount"] if r["type"] == "payment" else -r["amount"] for r in rows), Decimal(0))
    )
    ledger_total = money(sum((t.signed_amount for t in ledger_by_ref.values()), Decimal(0)))
    is_clean = not (mismatches or missing_in_partner or missing_in_ledger)

    report = ReconciliationReport.objects.create(
        batch=batch,
        source_name=source_name,
        partner_rows=len(rows),
        matched=matched,
        missing_in_partner=sorted(missing_in_partner),
        missing_in_ledger=missing_in_ledger,
        amount_mismatches=mismatches,
        partner_total=partner_total,
        ledger_total=ledger_total,
        is_clean=is_clean,
    )
    batch.reconciliation_status = "clean" if is_clean else "discrepancy"
    batch.save(update_fields=["reconciliation_status"])
    return report
