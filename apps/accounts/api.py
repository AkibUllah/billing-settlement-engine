from rest_framework import viewsets

from apps.accounts.models import Merchant, Partner
from apps.accounts.serializers import MerchantSerializer, PartnerSerializer


class PartnerViewSet(viewsets.ModelViewSet):
    queryset = Partner.objects.select_related("fee_schedule")
    serializer_class = PartnerSerializer
    filterset_fields = ["type", "is_active", "currency"]
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name", "created_at"]


class MerchantViewSet(viewsets.ModelViewSet):
    queryset = Merchant.objects.select_related("fee_schedule")
    serializer_class = MerchantSerializer
    filterset_fields = ["is_active", "fee_schedule"]
    search_fields = ["code", "name", "legal_name"]
    ordering_fields = ["code", "name", "created_at"]
