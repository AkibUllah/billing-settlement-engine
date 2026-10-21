from rest_framework import serializers

from apps.ledger.models import Account, JournalEntry, Posting


class AccountSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            "id",
            "code",
            "name",
            "type",
            "currency",
            "partner",
            "merchant",
            "is_active",
            "balance",
        ]

    def get_balance(self, obj) -> str:
        return str(obj.balance())


class PostingSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)

    class Meta:
        model = Posting
        fields = ["account", "account_code", "direction", "amount", "memo"]


class JournalEntrySerializer(serializers.ModelSerializer):
    postings = PostingSerializer(many=True, read_only=True)

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "reference",
            "description",
            "source",
            "posted_at",
            "status",
            "reverses",
            "postings",
        ]
