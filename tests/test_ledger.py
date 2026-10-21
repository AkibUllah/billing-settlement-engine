from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.ledger.models import Account, JournalEntry, Posting
from apps.ledger.services import (
    LedgerError,
    UnbalancedJournalError,
    credit,
    debit,
    partner_receivable,
    post_journal,
    reverse_journal,
    trial_balance,
)

pytestmark = pytest.mark.django_db


def test_balanced_journal_posts_and_updates_balances(chart):
    bank, revenue = chart["1000"], chart["4000"]
    journal = post_journal(
        reference="J-1",
        description="Fee received",
        lines=[
            debit(bank, Decimal("115.00")),
            credit(revenue, Decimal("100")),
            credit(chart["2100"], "15"),
        ],
        source="test",
    )
    assert journal.postings.count() == 3
    assert journal.totals() == (Decimal("115.00"), Decimal("115.00"))
    assert bank.balance() == Decimal("115.00")  # asset: debit-normal
    assert revenue.balance() == Decimal("100.00")  # revenue: credit-normal, shown positive
    assert chart["2100"].balance() == Decimal("15.00")


def test_unbalanced_journal_is_rejected_and_nothing_persisted(chart):
    with pytest.raises(UnbalancedJournalError, match="debits 100.00 != credits 90.00"):
        post_journal(
            reference="J-bad",
            description="oops",
            lines=[debit(chart["1000"], "100"), credit(chart["4000"], "90")],
        )
    assert JournalEntry.objects.count() == 0
    assert Posting.objects.count() == 0


def test_negative_zero_and_duplicate_journals_rejected(chart):
    with pytest.raises(LedgerError, match="negative"):
        post_journal(
            reference="J-neg",
            description="",
            lines=[debit(chart["1000"], "-5"), credit(chart["4000"], "-5")],
        )
    with pytest.raises(LedgerError, match="non-zero"):
        post_journal(reference="J-zero", description="", lines=[debit(chart["1000"], "0")])
    post_journal(
        reference="J-dup",
        description="",
        lines=[debit(chart["1000"], "1"), credit(chart["4000"], "1")],
    )
    with pytest.raises(LedgerError, match="already posted"):
        post_journal(
            reference="J-dup",
            description="",
            lines=[debit(chart["1000"], "1"), credit(chart["4000"], "1")],
        )


def test_mixed_currency_journal_rejected(chart):
    usd = Account.objects.create(
        code="1001", name="Bank USD", type=Account.Type.ASSET, currency="USD"
    )
    with pytest.raises(LedgerError, match="mixed currencies"):
        post_journal(
            reference="J-fx", description="", lines=[debit(usd, "10"), credit(chart["4000"], "10")]
        )


def test_posting_amount_positive_constraint_at_db_level(chart):
    journal = JournalEntry.objects.create(
        reference="J-raw", description="raw", posted_at="2026-10-20T00:00:00Z"
    )
    with pytest.raises(IntegrityError):
        Posting.objects.create(
            journal=journal, account=chart["1000"], direction="debit", amount=Decimal("-1")
        )


def test_model_level_validate_catches_tampering(chart):
    journal = post_journal(
        reference="J-v",
        description="",
        lines=[debit(chart["1000"], "50"), credit(chart["4000"], "50")],
    )
    Posting.objects.create(
        journal=journal, account=chart["1000"], direction="debit", amount=Decimal("1")
    )
    with pytest.raises(ValidationError, match="unbalanced"):
        journal.validate()


def test_reversal_flips_lines_and_marks_original(chart):
    original = post_journal(
        reference="J-o",
        description="",
        lines=[debit(chart["1000"], "80"), credit(chart["4000"], "80")],
    )
    reversal = reverse_journal(original, reference="J-o:REV")
    original.refresh_from_db()
    assert original.status == JournalEntry.Status.REVERSED
    assert reversal.reverses == original
    assert chart["1000"].balance() == Decimal("0.00")
    assert chart["4000"].balance() == Decimal("0.00")
    with pytest.raises(LedgerError, match="already reversed"):
        reverse_journal(original, reference="J-o:REV2")


def test_trial_balance_balances_and_skips_zero_accounts(chart, bkash):
    receivable = partner_receivable(bkash)
    assert receivable.code == "1100-BKASH" and receivable.partner == bkash
    post_journal(
        reference="J-tb",
        description="",
        lines=[
            debit(receivable, "1000"),
            debit(chart["5000"], "18.50"),
            credit(chart["2000"], "1018.50"),
        ],
    )
    tb = trial_balance()
    codes = [r["code"] for r in tb["rows"]]
    assert codes == ["1100-BKASH", "2000", "5000"]
    assert tb["total_debit"] == tb["total_credit"] == Decimal("1018.50")
    assert tb["balanced"] is True
    assert "1000" not in codes  # zero balance omitted
