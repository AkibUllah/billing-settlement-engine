from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from apps.accounts.api import MerchantViewSet, PartnerViewSet
from apps.billing.api import FeeScheduleViewSet, InvoiceViewSet, TransactionViewSet
from apps.ledger.api import AccountViewSet, JournalEntryViewSet, TrialBalanceView
from apps.settlement.api import ReconciliationReportViewSet, SettlementBatchViewSet

router = DefaultRouter()
router.register("partners", PartnerViewSet, basename="partner")
router.register("merchants", MerchantViewSet, basename="merchant")
router.register("fee-schedules", FeeScheduleViewSet, basename="fee-schedule")
router.register("transactions", TransactionViewSet, basename="transaction")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("settlements/batches", SettlementBatchViewSet, basename="settlement-batch")
router.register(
    "settlements/reconciliations", ReconciliationReportViewSet, basename="reconciliation"
)
router.register("ledger/accounts", AccountViewSet, basename="ledger-account")
router.register("ledger/journals", JournalEntryViewSet, basename="ledger-journal")


def health(_request):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok", "version": "0.1.0"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/token/", obtain_auth_token, name="api-token"),
    path("api/ledger/trial-balance/", TrialBalanceView.as_view(), name="trial-balance"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/", include(router.urls)),
]
