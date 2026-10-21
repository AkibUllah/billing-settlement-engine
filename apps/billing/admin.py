from django.contrib import admin

from apps.billing.models import FeeSchedule, FeeTier, Invoice, InvoiceLine, Transaction


class FeeTierInline(admin.TabularInline):
    model = FeeTier
    extra = 0


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "fee_type",
        "percentage",
        "fixed_amount",
        "min_fee",
        "max_fee",
        "vat_rate",
        "is_active",
    )
    list_filter = ("fee_type", "is_active")
    search_fields = ("name",)
    inlines = [FeeTierInline]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "external_ref",
        "type",
        "merchant",
        "partner",
        "amount",
        "partner_fee",
        "merchant_fee",
        "status",
        "occurred_at",
    )
    list_filter = ("type", "status", "partner", "merchant")
    search_fields = ("external_ref", "customer_msisdn")
    date_hierarchy = "occurred_at"
    readonly_fields = (
        "partner_fee",
        "partner_vat",
        "merchant_fee",
        "merchant_vat",
        "settlement_batch",
        "invoice",
    )


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    readonly_fields = ("description", "quantity", "unit_amount", "amount", "is_vat")
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "merchant",
        "period_start",
        "period_end",
        "subtotal",
        "vat_amount",
        "total",
        "status",
    )
    list_filter = ("status", "merchant")
    search_fields = ("number",)
    inlines = [InvoiceLineInline]
    readonly_fields = ("subtotal", "vat_amount", "total", "journal", "issued_at", "paid_at")
