"""Settlement batches: what each partner owes us for a cutoff, and the approval workflow."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common import ZERO


class SettlementBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"

    TRANSITIONS = {
        Status.PENDING: {Status.APPROVED},
        Status.APPROVED: {Status.PAID},
        Status.PAID: set(),
    }

    reference = models.CharField(max_length=64, unique=True)
    partner = models.ForeignKey(
        "accounts.Partner", on_delete=models.PROTECT, related_name="settlement_batches"
    )
    cutoff_date = models.DateField(help_text="Transactions up to and including this date")
    currency = models.CharField(max_length=3, default="BDT")
    transaction_count = models.PositiveIntegerField(default=0)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    vat_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    net_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO, help_text="gross - refunds - fee - vat"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reconciliation_status = models.CharField(
        max_length=16,
        choices=[
            ("unreconciled", "Unreconciled"),
            ("clean", "Clean"),
            ("discrepancy", "Discrepancy"),
        ],
        default="unreconciled",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=64, blank=True)
    approval_journal = models.ForeignKey(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    payment_journal = models.ForeignKey(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cutoff_date", "partner__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "cutoff_date"], name="unique_batch_per_partner_cutoff"
            )
        ]

    def __str__(self) -> str:
        return self.reference

    def can_transition(self, target: str) -> bool:
        return target in self.TRANSITIONS[self.Status(self.status)]


class ReconciliationReport(models.Model):
    batch = models.ForeignKey(
        SettlementBatch, on_delete=models.CASCADE, related_name="reconciliations"
    )
    source_name = models.CharField(max_length=200, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    partner_rows = models.PositiveIntegerField(default=0)
    matched = models.PositiveIntegerField(default=0)
    missing_in_partner = models.JSONField(default=list)
    missing_in_ledger = models.JSONField(default=list)
    amount_mismatches = models.JSONField(default=list)
    partner_total = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    ledger_total = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    is_clean = models.BooleanField(default=False)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self) -> str:
        return f"Reconciliation of {self.batch.reference} at {self.imported_at:%Y-%m-%d %H:%M}"
