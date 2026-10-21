from django.contrib import admin

from apps.settlement.models import ReconciliationReport, SettlementBatch


class ReconciliationInline(admin.TabularInline):
    model = ReconciliationReport
    extra = 0
    readonly_fields = (
        "imported_at",
        "partner_rows",
        "matched",
        "partner_total",
        "ledger_total",
        "is_clean",
    )
    can_delete = False


@admin.register(SettlementBatch)
class SettlementBatchAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "partner",
        "cutoff_date",
        "transaction_count",
        "gross_amount",
        "refund_amount",
        "fee_amount",
        "vat_amount",
        "net_amount",
        "status",
        "reconciliation_status",
    )
    list_filter = ("status", "reconciliation_status", "partner")
    search_fields = ("reference",)
    date_hierarchy = "cutoff_date"
    readonly_fields = (
        "reference",
        "transaction_count",
        "gross_amount",
        "refund_amount",
        "fee_amount",
        "vat_amount",
        "net_amount",
        "approval_journal",
        "payment_journal",
        "approved_by",
        "approved_at",
        "paid_at",
    )
    inlines = [ReconciliationInline]


@admin.register(ReconciliationReport)
class ReconciliationReportAdmin(admin.ModelAdmin):
    list_display = (
        "batch",
        "imported_at",
        "partner_rows",
        "matched",
        "is_clean",
        "partner_total",
        "ledger_total",
    )
    list_filter = ("is_clean",)
    readonly_fields = [f.name for f in ReconciliationReport._meta.fields]
