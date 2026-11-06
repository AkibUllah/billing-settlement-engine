from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.payouts.models import PayoutBatch, PayoutLine
from apps.payouts.serializers import (
    BankReferenceSerializer,
    CancelBatchSerializer,
    LineReasonSerializer,
    PayoutBatchDetailSerializer,
    PayoutBatchSerializer,
    PayoutLineSerializer,
    RunPayoutSerializer,
)
from apps.payouts.services import (
    approve_batch,
    bank_file_content,
    bank_file_name,
    cancel_batch,
    create_batch,
    export_batch,
    mark_batch_paid,
    mark_line_failed,
    mark_line_paid,
    mark_line_returned,
)

LINE_RELATIONS = ("merchant", "allocations__settlement_batch")


def _validated(serializer_class, request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class PayoutBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PayoutBatch.objects.select_related("approved_by")
    filterset_fields = ["status", "value_date", "currency"]
    search_fields = ["reference"]
    ordering_fields = ["value_date", "total_amount", "created_at"]

    def get_serializer_class(self):
        return PayoutBatchSerializer if self.action == "list" else PayoutBatchDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            return queryset
        return queryset.prefetch_related(*[f"lines__{name}" for name in LINE_RELATIONS])

    def _detail(self, batch, **kwargs) -> Response:
        batch = self.get_queryset().get(pk=batch.pk)
        return Response(PayoutBatchDetailSerializer(batch).data, **kwargs)

    @extend_schema(request=RunPayoutSerializer, responses={201: PayoutBatchDetailSerializer})
    @action(detail=False, methods=["post"])
    def run(self, request):
        """Draft a batch paying each eligible merchant their full payable balance."""
        data = _validated(RunPayoutSerializer, request)
        batch = create_batch(
            data.get("value_date"),
            minimum_amount=data["minimum_amount"],
            merchants=data.get("merchants") or None,
            user=request.user,
        )
        return self._detail(batch, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={200: PayoutBatchDetailSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Post one Dr merchant payable / Cr bank journal per line."""
        return self._detail(approve_batch(self.get_object(), user=request.user))

    @extend_schema(request=CancelBatchSerializer, responses={200: PayoutBatchDetailSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel a draft or approved batch, reversing any posted journals."""
        data = _validated(CancelBatchSerializer, request)
        return self._detail(cancel_batch(self.get_object(), data["reason"]))

    @extend_schema(request=None, responses={200: PayoutBatchDetailSerializer})
    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        """Generate and store the DCN1 file; download it from bank-file/."""
        return self._detail(export_batch(self.get_object()))

    @extend_schema(responses={(200, "text/csv"): OpenApiTypes.STR})
    @action(detail=True, methods=["get"], url_path="bank-file")
    def bank_file(self, request, pk=None):
        batch = self.get_object()
        response = HttpResponse(bank_file_content(batch), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{bank_file_name(batch)}"'
        return response

    @extend_schema(request=BankReferenceSerializer, responses={200: PayoutBatchDetailSerializer})
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        """Confirm every line still pending."""
        data = _validated(BankReferenceSerializer, request)
        return self._detail(mark_batch_paid(self.get_object(), data["bank_reference"]))


class PayoutLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PayoutLine.objects.select_related("merchant").prefetch_related(
        "allocations__settlement_batch"
    )
    serializer_class = PayoutLineSerializer
    filterset_fields = ["batch", "merchant", "status"]
    search_fields = ["reference", "bank_reference"]
    ordering_fields = ["amount", "created_at"]

    def _detail(self, line) -> Response:
        return Response(PayoutLineSerializer(self.get_queryset().get(pk=line.pk)).data)

    @extend_schema(request=BankReferenceSerializer, responses={200: PayoutLineSerializer})
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        data = _validated(BankReferenceSerializer, request)
        return self._detail(mark_line_paid(self.get_object(), data["bank_reference"]))

    @extend_schema(request=LineReasonSerializer, responses={200: PayoutLineSerializer})
    @action(detail=True, methods=["post"])
    def fail(self, request, pk=None):
        """The bank rejected the entry: reverse the journal and free the settlements."""
        data = _validated(LineReasonSerializer, request)
        return self._detail(mark_line_failed(self.get_object(), data["reason"]))

    @extend_schema(request=LineReasonSerializer, responses={200: PayoutLineSerializer})
    @action(detail=True, methods=["post"], url_path="return")
    def return_line(self, request, pk=None):
        """A paid credit came back: reverse the journal and free the settlements."""
        data = _validated(LineReasonSerializer, request)
        return self._detail(mark_line_returned(self.get_object(), data["reason"]))
