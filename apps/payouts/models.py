"""Merchant payouts: the other side of the payable that settlement approval credits.

A batch holds one line per merchant. Each line is posted to the ledger on its own, so a single
failed or returned payment reverses without touching the rest of the batch.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common import ZERO


class PayoutBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        EXPORTED = "exported", "Exported"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    # PAID closes the batch: every line has an outcome. Some of them may be failed or returned.
    TRANSITIONS = {
        Status.DRAFT: {Status.APPROVED, Status.CANCELLED},
        Status.APPROVED: {Status.EXPORTED, Status.CANCELLED},
        Status.EXPORTED: {Status.PAID},
        Status.PAID: set(),
        Status.CANCELLED: set(),
    }

    reference = models.CharField(max_length=64, unique=True)
    value_date = models.DateField(help_text="Date the bank should credit the merchants")
    currency = models.CharField(max_length=3, default="BDT")
    minimum_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO, help_text="Smaller balances are left to grow"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    line_count = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    skipped = models.JSONField(
        default=list, blank=True, help_text="Merchants left out at creation, with the reason"
    )
    bank_file = models.TextField(blank=True, help_text="The file exactly as it was exported")
    bank_file_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-value_date", "-id"]
        verbose_name_plural = "payout batches"

    def __str__(self) -> str:
        return self.reference

    def can_transition(self, target: str) -> bool:
        return target in self.TRANSITIONS[self.Status(self.status)]


class PayoutLine(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        RETURNED = "returned", "Returned"
        CANCELLED = "cancelled", "Cancelled"

    # FAILED: the bank rejected it, no money arrived. RETURNED: credited, then sent back.
    TRANSITIONS = {
        Status.PENDING: {Status.PAID, Status.FAILED, Status.CANCELLED},
        Status.PAID: {Status.RETURNED},
        Status.FAILED: set(),
        Status.RETURNED: set(),
        Status.CANCELLED: set(),
    }

    batch = models.ForeignKey(PayoutBatch, on_delete=models.PROTECT, related_name="lines")
    merchant = models.ForeignKey(
        "accounts.Merchant", on_delete=models.PROTECT, related_name="payout_lines"
    )
    sequence = models.PositiveIntegerField(help_text="1-based position in the bank file")
    reference = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    # Snapshot: editing a merchant later must not change a payment already in flight.
    routing_number = models.CharField(max_length=9)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=35)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    journal = models.OneToOneField(
        "ledger.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payout_line",
    )
    reversal_journal = models.OneToOneField(
        "ledger.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversed_payout_line",
    )
    bank_reference = models.CharField(max_length=64, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch_id", "sequence"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payout_line_amount_positive"),
            models.UniqueConstraint(
                fields=["batch", "sequence"], name="unique_payout_line_sequence"
            ),
            models.UniqueConstraint(
                fields=["batch", "merchant"], name="unique_payout_line_merchant"
            ),
            # Until approval posts the journal, the amount is still in the payable balance, so a
            # second unposted line for the merchant would promise the same money twice.
            models.UniqueConstraint(
                fields=["merchant"],
                condition=Q(status="pending", journal__isnull=True),
                name="unique_unposted_payout_line_per_merchant",
            ),
        ]

    def __str__(self) -> str:
        return self.reference

    def can_transition(self, target: str) -> bool:
        return target in self.TRANSITIONS[self.Status(self.status)]


class PayoutAllocation(models.Model):
    """Records that a payout line pays out a merchant's share of a settlement batch.

    A line drains the whole payable balance, so it covers every settlement credited to that
    balance and not yet covered by another line. `released_at` is set when the line's money comes
    back (failed, returned, cancelled); the settlement is then free to be paid again.
    """

    line = models.ForeignKey(PayoutLine, on_delete=models.PROTECT, related_name="allocations")
    settlement_batch = models.ForeignKey(
        "settlement.SettlementBatch", on_delete=models.PROTECT, related_name="payout_allocations"
    )
    merchant = models.ForeignKey(
        "accounts.Merchant", on_delete=models.PROTECT, related_name="payout_allocations"
    )
    credited_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="What the settlement credited to the payable, before any invoices deducted",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["settlement_batch", "merchant"],
                condition=Q(released_at__isnull=True),
                name="unique_live_payout_allocation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.line.reference} covers {self.settlement_batch.reference}"
