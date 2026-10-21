from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger.models import Account, JournalEntry
from apps.ledger.serializers import AccountSerializer, JournalEntrySerializer
from apps.ledger.services import trial_balance


class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    filterset_fields = ["type", "currency", "partner", "merchant", "is_active"]
    search_fields = ["code", "name"]


class JournalEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = JournalEntry.objects.prefetch_related("postings__account")
    serializer_class = JournalEntrySerializer
    filterset_fields = ["status", "source"]
    search_fields = ["reference", "description"]
    ordering_fields = ["posted_at"]


class TrialBalanceView(APIView):
    @extend_schema(responses={200: dict}, description="Trial balance of all active accounts")
    def get(self, request):
        as_of = (
            parse_datetime(request.query_params.get("as_of", ""))
            if request.query_params.get("as_of")
            else None
        )
        data = trial_balance(as_of)
        data["rows"] = [
            {**r, "debit": str(r["debit"]), "credit": str(r["credit"])} for r in data["rows"]
        ]
        data["total_debit"] = str(data["total_debit"])
        data["total_credit"] = str(data["total_credit"])
        return Response(data)
