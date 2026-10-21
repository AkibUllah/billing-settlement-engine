from rest_framework import serializers

from apps.accounts.models import Merchant, Partner


class PartnerSerializer(serializers.ModelSerializer):
    fee_schedule_name = serializers.CharField(source="fee_schedule.name", read_only=True)

    class Meta:
        model = Partner
        fields = [
            "id",
            "code",
            "name",
            "type",
            "currency",
            "settlement_account",
            "contact_email",
            "fee_schedule",
            "fee_schedule_name",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class MerchantSerializer(serializers.ModelSerializer):
    fee_schedule_name = serializers.CharField(source="fee_schedule.name", read_only=True)

    class Meta:
        model = Merchant
        fields = [
            "id",
            "code",
            "name",
            "legal_name",
            "bin_number",
            "address",
            "contact_email",
            "fee_schedule",
            "fee_schedule_name",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["created_at"]
