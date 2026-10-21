"""Fee calculation, transaction recording and invoice generation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.models import Merchant, Partner
from apps.billing.models import FeeSchedule, Invoice, InvoiceLine, Transaction
from apps.common import ZERO, format_bdt, money, percent_of
from apps.ledger import services as ledger


class BillingError(Exception):
    pass


@dataclass(frozen=True)
class FeeBreakdown:
    base_amount: Decimal
    fee: Decimal
    vat: Decimal
    schedule: str
    detail: str

    @property
    def total(self) -> Decimal:
        return money(self.fee + self.vat)


def _tiered_fee(schedule: FeeSchedule, amount: Decimal) -> tuple[Decimal, str]:
    tiers = list(schedule.tiers.all())
    if not tiers:
        raise BillingError(f"tiered schedule {schedule.name} has no tiers")
    if schedule.tier_mode == FeeSchedule.TierMode.SLAB:
        for tier in tiers:
            if tier.contains(amount):
                fee = percent_of(amount, tier.percentage) + tier.fixed_amount
                return money(fee), f"slab {tier}"
        raise BillingError(f"no tier covers amount {amount} in {schedule.name}")
    # marginal: each band charges its own percentage on the portion inside the band
    fee = ZERO
    parts = []
    top_tier = tiers[0]
    for tier in tiers:
        if amount <= tier.lower_bound:
            break
        upper = tier.upper_bound if tier.upper_bound is not None else amount
        portion = min(amount, upper) - tier.lower_bound
        if portion <= 0:
            continue
        fee += Decimal(portion) * tier.percentage / Decimal(100)
        parts.append(f"{money(portion)}@{tier.percentage}%")
        top_tier = tier
    fee = money(fee) + top_tier.fixed_amount
    return money(fee), "marginal " + " + ".join(parts)


def calculate_fee(schedule: FeeSchedule | None, amount: Decimal) -> FeeBreakdown:
    amount = money(amount)
    if amount < 0:
        raise BillingError("amount cannot be negative")
    if schedule is None:
        return FeeBreakdown(amount, ZERO, ZERO, "none", "no schedule")

    if schedule.fee_type == FeeSchedule.FeeType.PERCENTAGE:
        fee, detail = percent_of(amount, schedule.percentage), f"{schedule.percentage:.4f}%"
    elif schedule.fee_type == FeeSchedule.FeeType.FIXED:
        fee, detail = money(schedule.fixed_amount), f"fixed {money(schedule.fixed_amount)}"
    elif schedule.fee_type == FeeSchedule.FeeType.PERCENTAGE_PLUS_FIXED:
        fee = percent_of(amount, schedule.percentage) + money(schedule.fixed_amount)
        detail = f"{schedule.percentage:.4f}% + {money(schedule.fixed_amount)}"
    elif schedule.fee_type == FeeSchedule.FeeType.TIERED:
        fee, detail = _tiered_fee(schedule, amount)
    else:  # pragma: no cover
        raise BillingError(f"unknown fee type {schedule.fee_type}")

    if schedule.min_fee and fee < schedule.min_fee:
        fee, detail = money(schedule.min_fee), f"{detail}, floored to min {schedule.min_fee}"
    if schedule.max_fee is not None and fee > schedule.max_fee:
        fee, detail = money(schedule.max_fee), f"{detail}, capped at max {schedule.max_fee}"
    fee = money(fee)
    vat = percent_of(fee, schedule.vat_rate)
    return FeeBreakdown(amount, fee, vat, schedule.name, detail)


@db_transaction.atomic
def record_transaction(
    *,
    external_ref: str,
    merchant: Merchant,
    partner: Partner,
    amount: Decimal,
    type: str = Transaction.Type.PAYMENT,
    occurred_at: datetime | None = None,
    currency: str = "BDT",
    customer_msisdn: str = "",
) -> Transaction:
    """Persist a transaction with fees frozen from the current schedules. Refunds carry no fees."""
    amount = money(amount)
    if amount <= 0:
        raise BillingError("transaction amount must be positive")
    if type == Transaction.Type.PAYMENT:
        partner_fee = calculate_fee(partner.fee_schedule, amount)
        merchant_fee = calculate_fee(merchant.fee_schedule, amount)
    else:
        partner_fee = merchant_fee = FeeBreakdown(amount, ZERO, ZERO, "none", "refund")
    return Transaction.objects.create(
        external_ref=external_ref,
        merchant=merchant,
        partner=partner,
        type=type,
        amount=amount,
        currency=currency,
        occurred_at=occurred_at or timezone.now(),
        partner_fee=partner_fee.fee,
        partner_vat=partner_fee.vat,
        merchant_fee=merchant_fee.fee,
        merchant_vat=merchant_fee.vat,
        customer_msisdn=customer_msisdn,
    )


def _period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end, time.max), tz),
    )


def next_invoice_number(merchant: Merchant, period_end: date) -> str:
    prefix = f"INV-{period_end:%Y%m}-{merchant.code.upper()}"
    count = Invoice.objects.filter(number__startswith=prefix).count()
    return f"{prefix}-{count + 1:03d}"


@db_transaction.atomic
def generate_invoice(merchant: Merchant, period_start: date, period_end: date) -> Invoice:
    """Bill the merchant for fees on payments in the period. One invoice per merchant per period."""
    if period_end < period_start:
        raise BillingError("period_end before period_start")
    if Invoice.objects.filter(
        merchant=merchant, period_start=period_start, period_end=period_end
    ).exists():
        raise BillingError(
            f"invoice for {merchant.code} {period_start}..{period_end} already exists"
        )

    start, end = _period_bounds(period_start, period_end)
    txns = Transaction.objects.filter(
        merchant=merchant,
        type=Transaction.Type.PAYMENT,
        invoice__isnull=True,
        occurred_at__range=(start, end),
    )
    agg = txns.aggregate(
        count=Count("id"), gross=Sum("amount"), fees=Sum("merchant_fee"), vat=Sum("merchant_vat")
    )
    count = agg["count"] or 0
    gross = money(agg["gross"] or ZERO)
    fees = money(agg["fees"] or ZERO)
    vat = money(agg["vat"] or ZERO)

    invoice = Invoice.objects.create(
        number=next_invoice_number(merchant, period_end),
        merchant=merchant,
        period_start=period_start,
        period_end=period_end,
        subtotal=fees,
        vat_amount=vat,
        total=money(fees + vat),
    )
    if count:
        vat_rate = merchant.fee_schedule.vat_rate if merchant.fee_schedule else 0
        InvoiceLine.objects.create(
            invoice=invoice,
            description=f"Processing fees on {count} payments totalling {format_bdt(gross)}",
            quantity=count,
            unit_amount=money(fees / count) if count else ZERO,
            amount=fees,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description=f"VAT on fees ({vat_rate}%)",
            quantity=1,
            unit_amount=vat,
            amount=vat,
            is_vat=True,
        )
        txns.update(invoice=invoice)
    return invoice


@db_transaction.atomic
def issue_invoice(invoice: Invoice) -> Invoice:
    """Issue: recognise fee revenue and VAT output, deducted from what we owe the merchant."""
    if invoice.status != Invoice.Status.DRAFT:
        raise BillingError(f"invoice {invoice.number} is {invoice.status}, not draft")
    if invoice.total > 0:
        invoice.journal = ledger.post_journal(
            reference=f"INV:{invoice.number}",
            description=f"Invoice {invoice.number} to {invoice.merchant.name}",
            source="billing:issue",
            lines=[
                ledger.debit(
                    ledger.merchant_payable(invoice.merchant), invoice.total, "fees deducted"
                ),
                ledger.credit(ledger.account("4000"), invoice.subtotal, "fee revenue"),
                ledger.credit(ledger.account("2100"), invoice.vat_amount, "VAT output"),
            ],
        )
    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["status", "issued_at", "journal"])
    return invoice


def render_invoice_text(invoice: Invoice) -> str:
    merchant = invoice.merchant
    lines = [
        f"INVOICE {invoice.number}",
        f"Status: {invoice.status.upper()}",
        "",
        f"Billed to: {merchant.legal_name or merchant.name}",
        f"BIN: {merchant.bin_number or '-'}",
        f"Address: {merchant.address or '-'}",
        f"Period: {invoice.period_start:%d %b %Y} – {invoice.period_end:%d %b %Y}",
        "",
        f"{'Description':<60} {'Qty':>5} {'Amount':>16}",
        "-" * 83,
    ]
    for line in invoice.lines.all():
        lines.append(
            f"{line.description[:60]:<60} {line.quantity:>5} {format_bdt(line.amount):>16}"
        )
    lines += [
        "-" * 83,
        f"{'Subtotal (fees)':<66} {format_bdt(invoice.subtotal):>16}",
        f"{'VAT':<66} {format_bdt(invoice.vat_amount):>16}",
        f"{'TOTAL':<66} {format_bdt(invoice.total):>16}",
    ]
    return "\n".join(lines) + "\n"


def render_invoice_csv(invoice: Invoice) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["invoice", "description", "quantity", "unit_amount", "amount", "is_vat"])
    for line in invoice.lines.all():
        writer.writerow(
            [
                invoice.number,
                line.description,
                line.quantity,
                line.unit_amount,
                line.amount,
                line.is_vat,
            ]
        )
    writer.writerow([invoice.number, "TOTAL", "", "", invoice.total, ""])
    return buffer.getvalue()
