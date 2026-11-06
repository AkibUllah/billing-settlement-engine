from django.contrib import admin

from apps.payouts.models import PayoutAllocation, PayoutBatch, PayoutLine


class ReadOnlyAdmin(admin.ModelAdmin):
    """Payouts carry ledger state, so they change only through the service layer."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PayoutLineInline(admin.TabularInline):
    model = PayoutLine
    extra = 0
    can_delete = False
    fields = ("sequence", "merchant", "amount", "status", "journal", "reversal_journal")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PayoutBatch)
class PayoutBatchAdmin(ReadOnlyAdmin):
    list_display = ("reference", "value_date", "status", "line_count", "total_amount")
    list_filter = ("status",)
    search_fields = ("reference",)
    date_hierarchy = "value_date"
    exclude = ("bank_file",)
    inlines = [PayoutLineInline]


@admin.register(PayoutLine)
class PayoutLineAdmin(ReadOnlyAdmin):
    list_display = ("reference", "merchant", "amount", "status", "bank_reference", "paid_at")
    list_filter = ("status",)
    search_fields = ("reference", "bank_reference", "merchant__code")


@admin.register(PayoutAllocation)
class PayoutAllocationAdmin(ReadOnlyAdmin):
    list_display = ("line", "settlement_batch", "merchant", "credited_amount", "released_at")
    search_fields = ("line__reference", "settlement_batch__reference", "merchant__code")
