from rest_framework import serializers

from apps.accounts.models import Partner
from apps.settlement.models import ReconciliationReport, SettlementBatch


class SettlementBatchSerializer(serializers.ModelSerializer):
    partner_code = serializers.CharField(source="partner.code", read_only=True)
    approved_by_username = serializers.CharField(
        source="approved_by.username", read_only=True, default=None
    )

    class Meta:
        model = SettlementBatch
        fields = [
            "id",
            "reference",
            "partner",
            "partner_code",
            "cutoff_date",
            "currency",
            "transaction_count",
            "gross_amount",
            "refund_amount",
            "fee_amount",
            "vat_amount",
            "net_amount",
            "status",
            "reconciliation_status",
            "approved_by",
            "approved_by_username",
            "approved_at",
            "paid_at",
            "payment_reference",
            "approval_journal",
            "payment_journal",
            "created_at",
        ]
        read_only_fields = fields


class RunSettlementSerializer(serializers.Serializer):
    cutoff_date = serializers.DateField()
    partners = serializers.PrimaryKeyRelatedField(
        queryset=Partner.objects.filter(is_active=True), many=True, required=False
    )


class MarkPaidSerializer(serializers.Serializer):
    payment_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )


class ReconcileUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="CSV with columns external_ref,type,amount")


class ReconciliationReportSerializer(serializers.ModelSerializer):
    batch_reference = serializers.CharField(source="batch.reference", read_only=True)

    class Meta:
        model = ReconciliationReport
        fields = [
            "id",
            "batch",
            "batch_reference",
            "source_name",
            "imported_at",
            "partner_rows",
            "matched",
            "missing_in_partner",
            "missing_in_ledger",
            "amount_mismatches",
            "partner_total",
            "ledger_total",
            "is_clean",
        ]
        read_only_fields = fields
