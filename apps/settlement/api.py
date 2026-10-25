from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.api_errors import DomainError
from apps.settlement.models import ReconciliationReport, SettlementBatch
from apps.settlement.serializers import (
    MarkPaidSerializer,
    ReconcileUploadSerializer,
    ReconciliationReportSerializer,
    RunSettlementSerializer,
    SettlementBatchSerializer,
)
from apps.settlement.services import (
    InvalidTransition,
    SettlementError,
    approve_batch,
    export_csv,
    mark_paid,
    reconcile,
    run_settlement,
)


class SettlementBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SettlementBatch.objects.select_related("partner", "approved_by")
    serializer_class = SettlementBatchSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    filterset_fields = ["partner", "status", "reconciliation_status", "cutoff_date", "currency"]
    search_fields = ["reference"]
    ordering_fields = ["cutoff_date", "net_amount", "created_at"]

    @extend_schema(
        request=RunSettlementSerializer, responses={201: SettlementBatchSerializer(many=True)}
    )
    @action(detail=False, methods=["post"])
    def run(self, request):
        """Create batches for every active partner (or the given ones) up to a cutoff date."""
        serializer = RunSettlementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batches = run_settlement(
            serializer.validated_data["cutoff_date"],
            serializer.validated_data.get("partners") or None,
        )
        return Response(
            SettlementBatchSerializer(batches, many=True).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(request=None, responses={200: SettlementBatchSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            batch = approve_batch(self.get_object(), user=request.user)
        except InvalidTransition as exc:
            raise DomainError(str(exc), status.HTTP_409_CONFLICT) from exc
        return Response(SettlementBatchSerializer(batch).data)

    @extend_schema(request=MarkPaidSerializer, responses={200: SettlementBatchSerializer})
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            batch = mark_paid(self.get_object(), serializer.validated_data["payment_reference"])
        except InvalidTransition as exc:
            raise DomainError(str(exc), status.HTTP_409_CONFLICT) from exc
        return Response(SettlementBatchSerializer(batch).data)

    @extend_schema(
        request=ReconcileUploadSerializer, responses={201: ReconciliationReportSerializer}
    )
    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        """Upload the partner's CSV report and diff it against the batch."""
        serializer = ReconcileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        try:
            report = reconcile(self.get_object(), upload.read(), source_name=upload.name)
        except SettlementError as exc:
            raise DomainError(str(exc), status.HTTP_400_BAD_REQUEST) from exc
        return Response(ReconciliationReportSerializer(report).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="export")
    def export(self, request, pk=None):
        batch = self.get_object()
        response = HttpResponse(export_csv(batch), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{batch.reference}.csv"'
        return response


class ReconciliationReportViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReconciliationReport.objects.select_related("batch")
    serializer_class = ReconciliationReportSerializer
    filterset_fields = ["batch", "is_clean"]
