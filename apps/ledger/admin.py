from django.contrib import admin

from apps.ledger.models import Account, JournalEntry, Posting


class PostingInline(admin.TabularInline):
    model = Posting
    extra = 0
    readonly_fields = ("account", "direction", "amount", "memo")
    can_delete = False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "type",
        "currency",
        "partner",
        "merchant",
        "current_balance",
        "is_active",
    )
    list_filter = ("type", "currency", "is_active")
    search_fields = ("code", "name")

    @admin.display(description="Balance")
    def current_balance(self, obj):
        return obj.balance()


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("reference", "description", "source", "posted_at", "status")
    list_filter = ("status", "source")
    search_fields = ("reference", "description")
    inlines = [PostingInline]
    readonly_fields = ("reference", "description", "source", "posted_at", "status", "reverses")


@admin.register(Posting)
class PostingAdmin(admin.ModelAdmin):
    list_display = ("journal", "account", "direction", "amount")
    list_filter = ("direction", "account__type")
    search_fields = ("journal__reference", "account__code")
