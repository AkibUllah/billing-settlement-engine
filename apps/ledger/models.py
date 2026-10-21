"""Double-entry ledger: accounts, journal entries and postings.

Invariant: every posted journal has sum(debits) == sum(credits). It is enforced in
`services.post_journal` (the only supported write path) and again in `JournalEntry.validate`.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, DecimalField, F, Sum, Value, When

from apps.common import ZERO, money


class Account(models.Model):
    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        REVENUE = "revenue", "Revenue"
        EXPENSE = "expense", "Expense"

    DEBIT_NORMAL = {Type.ASSET, Type.EXPENSE}

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=16, choices=Type.choices)
    currency = models.CharField(max_length=3, default="BDT")
    partner = models.ForeignKey(
        "accounts.Partner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_accounts",
    )
    merchant = models.ForeignKey(
        "accounts.Merchant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_accounts",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def debit_normal(self) -> bool:
        return self.type in self.DEBIT_NORMAL

    def balance(self, as_of=None) -> Decimal:
        """Signed balance in the account's normal direction (positive = expected sign).

        Reversed journals stay in the ledger; their reversal entry nets them to zero.
        """
        postings = self.postings.all()
        if as_of is not None:
            postings = postings.filter(journal__posted_at__lte=as_of)
        agg = postings.aggregate(
            debits=Sum(
                Case(
                    When(direction=Posting.Direction.DEBIT, then=F("amount")),
                    default=Value(Decimal("0")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
            credits=Sum(
                Case(
                    When(direction=Posting.Direction.CREDIT, then=F("amount")),
                    default=Value(Decimal("0")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
        )
        debits = agg["debits"] or ZERO
        credits = agg["credits"] or ZERO
        raw = debits - credits
        return money(raw if self.debit_normal else -raw)


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    reference = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=300)
    source = models.CharField(max_length=64, blank=True, help_text="e.g. settlement:approve")
    posted_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.POSTED)
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-posted_at", "-id"]

    def __str__(self) -> str:
        return f"{self.reference}: {self.description}"

    def totals(self) -> tuple[Decimal, Decimal]:
        debits = sum((p.amount for p in self.postings.all() if p.is_debit), ZERO)
        credits = sum((p.amount for p in self.postings.all() if not p.is_debit), ZERO)
        return money(debits), money(credits)

    def validate(self) -> None:
        debits, credits = self.totals()
        if debits != credits:
            raise ValidationError(
                f"journal {self.reference} is unbalanced: debits {debits} != credits {credits}"
            )
        if debits == ZERO:
            raise ValidationError(f"journal {self.reference} has no postings")


class Posting(models.Model):
    class Direction(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    journal = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="postings")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="postings")
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    memo = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name="posting_amount_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.account.code} {self.amount}"

    @property
    def is_debit(self) -> bool:
        return self.direction == self.Direction.DEBIT
