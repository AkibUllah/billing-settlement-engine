from django.contrib import admin

from apps.accounts.models import Merchant, Partner


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "currency", "fee_schedule", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "fee_schedule", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "legal_name", "bin_number")
