"""Partners (MFS operators, banks, aggregators we settle with) and the merchants we bill."""

from __future__ import annotations

from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Partner(TimeStamped):
    class Type(models.TextChoices):
        MFS = "mfs", "Mobile financial service"
        BANK = "bank", "Bank"
        AGGREGATOR = "aggregator", "Payment aggregator"

    code = models.SlugField(max_length=32, unique=True, help_text="Short stable code, e.g. BKASH")
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.MFS)
    currency = models.CharField(max_length=3, default="BDT")
    settlement_account = models.CharField(
        max_length=64, blank=True, help_text="Bank account / wallet the partner remits to"
    )
    contact_email = models.EmailField(blank=True)
    fee_schedule = models.ForeignKey(
        "billing.FeeSchedule",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partners",
        help_text="Commission the partner deducts from gross collections",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Merchant(TimeStamped):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    legal_name = models.CharField(max_length=200, blank=True)
    bin_number = models.CharField(
        "BIN", max_length=20, blank=True, help_text="Business identification number (VAT)"
    )
    address = models.CharField(max_length=300, blank=True)
    contact_email = models.EmailField(blank=True)
    fee_schedule = models.ForeignKey(
        "billing.FeeSchedule",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="merchants",
        help_text="Processing fee we charge the merchant",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"
