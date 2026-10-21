from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common import ZERO


class FeeSchedule(models.Model):
    class FeeType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed"
        PERCENTAGE_PLUS_FIXED = "percentage_plus_fixed", "Percentage + fixed"
        TIERED = "tiered", "Tiered"

    class TierMode(models.TextChoices):
        SLAB = "slab", "Slab (rate of the tier the amount falls in)"
        MARGINAL = "marginal", "Marginal (each tier applies to its own band)"

    name = models.CharField(max_length=120, unique=True)
    fee_type = models.CharField(max_length=32, choices=FeeType.choices)
    percentage = models.DecimalField(
        max_digits=7, decimal_places=4, default=ZERO, help_text="Percent, e.g. 1.8500"
    )
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    min_fee = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    max_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("15.00"), help_text="VAT % on the fee"
    )
    tier_mode = models.CharField(max_length=16, choices=TierMode.choices, default=TierMode.SLAB)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @staticmethod
    def default_vat_rate() -> Decimal:
        return settings.DEFAULT_VAT_RATE


class FeeTier(models.Model):
    schedule = models.ForeignKey(FeeSchedule, on_delete=models.CASCADE, related_name="tiers")
    lower_bound = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    upper_bound = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, help_text="Blank = no upper limit"
    )
    percentage = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO)
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["lower_bound"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "lower_bound"], name="unique_tier_lower_bound"
            )
        ]

    def __str__(self) -> str:
        upper = self.upper_bound if self.upper_bound is not None else "∞"
        return f"{self.lower_bound}–{upper}: {self.percentage}% + {self.fixed_amount}"

    def contains(self, amount: Decimal) -> bool:
        return amount >= self.lower_bound and (
            self.upper_bound is None or amount < self.upper_bound
        )


class Transaction(models.Model):
    class Type(models.TextChoices):
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"

    class Status(models.TextChoices):
        UNSETTLED = "unsettled", "Unsettled"
        SETTLED = "settled", "Settled"

    external_ref = models.CharField(max_length=64, unique=True, help_text="Partner trxID")
    merchant = models.ForeignKey(
        "accounts.Merchant", on_delete=models.PROTECT, related_name="transactions"
    )
    partner = models.ForeignKey(
        "accounts.Partner", on_delete=models.PROTECT, related_name="transactions"
    )
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.PAYMENT)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="BDT")
    occurred_at = models.DateTimeField()
    # Fees are frozen at ingestion so later schedule changes never rewrite history.
    partner_fee = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    partner_vat = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    merchant_fee = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    merchant_vat = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNSETTLED)
    settlement_batch = models.ForeignKey(
        "settlement.SettlementBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
    )
    invoice = models.ForeignKey(
        "billing.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
    )
    customer_msisdn = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["partner", "status", "occurred_at"]),
            models.Index(fields=["merchant", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.external_ref} {self.type} {self.amount} {self.currency}"

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.type == self.Type.PAYMENT else -self.amount


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(max_length=32, unique=True)
    merchant = models.ForeignKey(
        "accounts.Merchant", on_delete=models.PROTECT, related_name="invoices"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    currency = models.CharField(max_length=3, default="BDT")
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    vat_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    journal = models.ForeignKey(
        "ledger.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "period_start", "period_end"],
                name="unique_invoice_per_merchant_period",
            )
        ]

    def __str__(self) -> str:
        return self.number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=300)
    quantity = models.PositiveIntegerField(default=1)
    unit_amount = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    is_vat = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.description}: {self.amount}"
