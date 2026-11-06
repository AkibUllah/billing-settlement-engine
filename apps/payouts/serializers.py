from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import Merchant
from apps.payouts.models import PayoutBatch, PayoutLine


class PayoutLineSerializer(serializers.ModelSerializer):
    merchant_code = serializers.CharField(source="merchant.code", read_only=True)
    settlements = serializers.SerializerMethodField()

    class Meta:
        model = PayoutLine
        fields = [
            "id",
            "batch",
            "merchant",
            "merchant_code",
            "sequence",
            "reference",
            "amount",
            "routing_number",
            "account_number",
            "account_name",
            "status",
            "journal",
            "reversal_journal",
            "bank_reference",
            "failure_reason",
            "paid_at",
            "reversed_at",
            "settlements",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_settlements(self, line) -> list[str]:
        """References of the settlement batches this line covers."""
        return [a.settlement_batch.reference for a in line.allocations.all()]


class PayoutBatchSerializer(serializers.ModelSerializer):
    approved_by_username = serializers.CharField(
        source="approved_by.username", read_only=True, default=None
    )

    class Meta:
        model = PayoutBatch
        fields = [
            "id",
            "reference",
            "value_date",
            "currency",
            "minimum_amount",
            "status",
            "line_count",
            "total_amount",
            "skipped",
            "bank_file_sha256",
            "created_by",
            "approved_by",
            "approved_by_username",
            "approved_at",
            "exported_at",
            "paid_at",
            "cancelled_at",
            "created_at",
        ]
        read_only_fields = fields


class PayoutBatchDetailSerializer(PayoutBatchSerializer):
    lines = PayoutLineSerializer(many=True, read_only=True)
    outcomes = serializers.SerializerMethodField()

    class Meta(PayoutBatchSerializer.Meta):
        fields = [*PayoutBatchSerializer.Meta.fields, "outcomes", "lines"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_outcomes(self, batch) -> dict:
        """Line count and amount per status, e.g. {"paid": {"count": 2, "amount": "2700.00"}}."""
        outcomes: dict = {}
        for line in batch.lines.all():
            entry = outcomes.setdefault(line.status, {"count": 0, "amount": Decimal("0.00")})
            entry["count"] += 1
            entry["amount"] += line.amount
        return {
            status: {"count": entry["count"], "amount": f"{entry['amount']:.2f}"}
            for status, entry in sorted(outcomes.items())
        }


class RunPayoutSerializer(serializers.Serializer):
    value_date = serializers.DateField(required=False, help_text="Defaults to today")
    minimum_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        default=Decimal("0"),
    )
    merchants = serializers.PrimaryKeyRelatedField(
        queryset=Merchant.objects.filter(is_active=True), many=True, required=False
    )


class CancelBatchSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, default="cancelled")


class BankReferenceSerializer(serializers.Serializer):
    bank_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )


class LineReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)
