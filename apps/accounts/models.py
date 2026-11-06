"""Partners (MFS operators, banks, aggregators we settle with) and the merchants we bill."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

# Payout destinations are written into bank files, so they stay within a strict character set.
validate_routing_number = RegexValidator(r"^[0-9]{9}\Z", "Routing number must be exactly 9 digits.")
validate_account_number = RegexValidator(r"^[0-9]{6,20}\Z", "Account number must be 6-20 digits.")
validate_account_name = RegexValidator(
    r"^[A-Za-z0-9][A-Za-z0-9 .&'/()-]{0,34}\Z",
    "Account name must be 1-35 ASCII letters, digits, spaces or . & ' / ( ) -",
)


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
    bank_routing_number = models.CharField(
        max_length=9, blank=True, validators=[validate_routing_number]
    )
    bank_account_number = models.CharField(
        max_length=20, blank=True, validators=[validate_account_number]
    )
    bank_account_name = models.CharField(
        max_length=35, blank=True, validators=[validate_account_name]
    )
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

    def bank_detail_errors(self) -> list[str]:
        """Why this merchant cannot receive a bank payout; empty when the details are usable.

        `save()` does not run field validators, so payouts check them again at the point of use.
        """
        errors = []
        for field_name in ("bank_routing_number", "bank_account_number", "bank_account_name"):
            field = self._meta.get_field(field_name)
            value = getattr(self, field_name)
            if not value:
                errors.append(f"{field_name} is missing")
                continue
            try:
                field.run_validators(value)
            except ValidationError as exc:
                errors.append(f"{field_name}: {' '.join(exc.messages)}")
        return errors
