from decimal import Decimal

from rest_framework import serializers

from apps.billing.models import FeeSchedule, FeeTier, Invoice, InvoiceLine, Transaction
from apps.billing.services import record_transaction


class FeeTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeTier
        fields = ["id", "lower_bound", "upper_bound", "percentage", "fixed_amount"]


class FeeScheduleSerializer(serializers.ModelSerializer):
    tiers = FeeTierSerializer(many=True, required=False)

    class Meta:
        model = FeeSchedule
        fields = [
            "id",
            "name",
            "fee_type",
            "percentage",
            "fixed_amount",
            "min_fee",
            "max_fee",
            "vat_rate",
            "tier_mode",
            "tiers",
            "is_active",
        ]

    def create(self, validated_data):
        tiers = validated_data.pop("tiers", [])
        schedule = FeeSchedule.objects.create(**validated_data)
        for tier in tiers:
            FeeTier.objects.create(schedule=schedule, **tier)
        return schedule

    def update(self, instance, validated_data):
        tiers = validated_data.pop("tiers", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        if tiers is not None:
            instance.tiers.all().delete()
            for tier in tiers:
                FeeTier.objects.create(schedule=instance, **tier)
        return instance


class FeeQuoteSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))


class TransactionSerializer(serializers.ModelSerializer):
    merchant_code = serializers.CharField(source="merchant.code", read_only=True)
    partner_code = serializers.CharField(source="partner.code", read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "external_ref",
            "merchant",
            "merchant_code",
            "partner",
            "partner_code",
            "type",
            "amount",
            "currency",
            "occurred_at",
            "customer_msisdn",
            "partner_fee",
            "partner_vat",
            "merchant_fee",
            "merchant_vat",
            "status",
            "settlement_batch",
            "invoice",
            "created_at",
        ]
        read_only_fields = [
            "partner_fee",
            "partner_vat",
            "merchant_fee",
            "merchant_vat",
            "status",
            "settlement_batch",
            "invoice",
            "created_at",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("amount must be positive")
        return value

    def create(self, validated_data):
        return record_transaction(**validated_data)

    def update(self, instance, validated_data):
        raise serializers.ValidationError("transactions are immutable once recorded")


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["description", "quantity", "unit_amount", "amount", "is_vat"]


class InvoiceSerializer(serializers.ModelSerializer):
    merchant_code = serializers.CharField(source="merchant.code", read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "merchant",
            "merchant_code",
            "period_start",
            "period_end",
            "currency",
            "subtotal",
            "vat_amount",
            "total",
            "status",
            "issued_at",
            "paid_at",
            "journal",
            "lines",
            "created_at",
        ]
        read_only_fields = fields


class InvoiceGenerateSerializer(serializers.Serializer):
    merchant = serializers.PrimaryKeyRelatedField(
        queryset=Invoice.merchant.field.related_model.objects.all()
    )
    period_start = serializers.DateField()
    period_end = serializers.DateField()
