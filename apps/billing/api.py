from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.billing.models import FeeSchedule, Invoice, Transaction
from apps.billing.serializers import (
    FeeQuoteSerializer,
    FeeScheduleSerializer,
    InvoiceGenerateSerializer,
    InvoiceSerializer,
    TransactionSerializer,
)
from apps.billing.services import (
    BillingError,
    calculate_fee,
    generate_invoice,
    issue_invoice,
    render_invoice_csv,
    render_invoice_text,
)


class FeeScheduleViewSet(viewsets.ModelViewSet):
    queryset = FeeSchedule.objects.prefetch_related("tiers")
    serializer_class = FeeScheduleSerializer
    filterset_fields = ["fee_type", "is_active"]
    search_fields = ["name"]

    @extend_schema(request=FeeQuoteSerializer, responses={200: dict})
    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        """Preview fee + VAT for an amount under this schedule."""
        serializer = FeeQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            breakdown = calculate_fee(self.get_object(), serializer.validated_data["amount"])
        except BillingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "schedule": breakdown.schedule,
                "amount": str(breakdown.base_amount),
                "fee": str(breakdown.fee),
                "vat": str(breakdown.vat),
                "total": str(breakdown.total),
                "detail": breakdown.detail,
            }
        )


class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.select_related("merchant", "partner")
    serializer_class = TransactionSerializer
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = {
        "merchant": ["exact"],
        "partner": ["exact"],
        "type": ["exact"],
        "status": ["exact"],
        "settlement_batch": ["exact", "isnull"],
        "occurred_at": ["gte", "lte", "date"],
    }
    search_fields = ["external_ref", "customer_msisdn"]
    ordering_fields = ["occurred_at", "amount"]

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except BillingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.select_related("merchant").prefetch_related("lines")
    serializer_class = InvoiceSerializer
    filterset_fields = ["merchant", "status", "period_start", "period_end"]
    search_fields = ["number"]

    @extend_schema(request=InvoiceGenerateSerializer, responses={201: InvoiceSerializer})
    @action(detail=False, methods=["post"])
    def generate(self, request):
        serializer = InvoiceGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invoice = generate_invoice(**serializer.validated_data)
        except BillingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={200: InvoiceSerializer})
    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        try:
            invoice = issue_invoice(self.get_object())
        except BillingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get"])
    def text(self, request, pk=None):
        return HttpResponse(
            render_invoice_text(self.get_object()), content_type="text/plain; charset=utf-8"
        )

    @action(detail=True, methods=["get"], url_path="export")
    def export_csv(self, request, pk=None):
        invoice = self.get_object()
        response = HttpResponse(render_invoice_csv(invoice), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.csv"'
        return response
