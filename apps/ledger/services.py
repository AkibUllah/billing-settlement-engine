"""The write path for the ledger."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.common import ZERO, money
from apps.ledger.models import Account, JournalEntry, Posting


class LedgerError(Exception):
    pass


class UnbalancedJournalError(LedgerError):
    pass


@dataclass(frozen=True)
class Line:
    account: Account
    direction: str  # Posting.Direction
    amount: Decimal
    memo: str = ""


def debit(account: Account, amount: Decimal, memo: str = "") -> Line:
    return Line(account, Posting.Direction.DEBIT, money(amount), memo)


def credit(account: Account, amount: Decimal, memo: str = "") -> Line:
    return Line(account, Posting.Direction.CREDIT, money(amount), memo)


@transaction.atomic
def post_journal(
    *,
    reference: str,
    description: str,
    lines: Iterable[Line],
    source: str = "",
    posted_at: datetime | None = None,
) -> JournalEntry:
    """Create a balanced journal entry or raise. Zero lines are dropped; negatives rejected."""
    lines = [line for line in lines if line.amount != ZERO]
    if not lines:
        raise LedgerError("a journal needs at least one non-zero line")
    for line in lines:
        if line.amount < 0:
            raise LedgerError(f"negative posting amount {line.amount} on {line.account.code}")
    currencies = {line.account.currency for line in lines}
    if len(currencies) > 1:
        raise LedgerError(f"mixed currencies in one journal: {sorted(currencies)}")
    debits = sum((ln.amount for ln in lines if ln.direction == Posting.Direction.DEBIT), ZERO)
    credits = sum((ln.amount for ln in lines if ln.direction == Posting.Direction.CREDIT), ZERO)
    if debits != credits:
        raise UnbalancedJournalError(
            f"debits {money(debits)} != credits {money(credits)} for {reference}"
        )
    if JournalEntry.objects.filter(reference=reference).exists():
        raise LedgerError(f"journal reference {reference} already posted")

    journal = JournalEntry.objects.create(
        reference=reference,
        description=description,
        source=source,
        posted_at=posted_at or timezone.now(),
    )
    Posting.objects.bulk_create(
        [
            Posting(
                journal=journal,
                account=line.account,
                direction=line.direction,
                amount=line.amount,
                memo=line.memo,
            )
            for line in lines
        ]
    )
    journal.validate()
    return journal


@transaction.atomic
def reverse_journal(journal: JournalEntry, reference: str, description: str = "") -> JournalEntry:
    if journal.status == JournalEntry.Status.REVERSED:
        raise LedgerError(f"{journal.reference} is already reversed")
    flipped = [
        Line(
            p.account,
            Posting.Direction.CREDIT if p.is_debit else Posting.Direction.DEBIT,
            p.amount,
            p.memo,
        )
        for p in journal.postings.select_related("account")
    ]
    reversal = post_journal(
        reference=reference,
        description=description or f"Reversal of {journal.reference}",
        lines=flipped,
        source="reversal",
    )
    reversal.reverses = journal
    reversal.save(update_fields=["reverses"])
    journal.status = JournalEntry.Status.REVERSED
    journal.save(update_fields=["status"])
    return reversal


def trial_balance(as_of: datetime | None = None) -> dict:
    rows = []
    total_debit = total_credit = ZERO
    for account in Account.objects.filter(is_active=True).order_by("code"):
        balance = account.balance(as_of)
        if balance == ZERO:
            continue
        # Present in debit/credit columns regardless of sign convention.
        natural = balance if account.debit_normal else -balance
        if natural >= 0:
            d, c = money(natural), ZERO
        else:
            d, c = ZERO, money(-natural)
        total_debit += d
        total_credit += c
        rows.append(
            {
                "code": account.code,
                "name": account.name,
                "type": account.type,
                "debit": d,
                "credit": c,
            }
        )
    return {
        "as_of": as_of,
        "rows": rows,
        "total_debit": money(total_debit),
        "total_credit": money(total_credit),
        "balanced": money(total_debit) == money(total_credit),
    }


# -- chart of accounts -----------------------------------------------------------------
CHART = {
    "1000": ("Bank - Operating", Account.Type.ASSET),
    "1300": ("VAT Input (recoverable)", Account.Type.ASSET),
    "2000": ("Merchant Payables", Account.Type.LIABILITY),
    "2100": ("VAT Output (payable)", Account.Type.LIABILITY),
    "4000": ("Merchant Fee Revenue", Account.Type.REVENUE),
    "5000": ("Partner Commission Expense", Account.Type.EXPENSE),
}


def ensure_chart_of_accounts() -> dict[str, Account]:
    accounts = {}
    for code, (name, type_) in CHART.items():
        accounts[code], _ = Account.objects.get_or_create(
            code=code, defaults={"name": name, "type": type_}
        )
    return accounts


def account(code: str) -> Account:
    ensure_chart_of_accounts()
    return Account.objects.get(code=code)


def partner_receivable(partner) -> Account:
    acct, _ = Account.objects.get_or_create(
        code=f"1100-{partner.code}",
        defaults={
            "name": f"Receivable - {partner.name}",
            "type": Account.Type.ASSET,
            "currency": partner.currency,
            "partner": partner,
        },
    )
    return acct


def merchant_payable(merchant) -> Account:
    acct, _ = Account.objects.get_or_create(
        code=f"2000-{merchant.code}",
        defaults={
            "name": f"Payable - {merchant.name}",
            "type": Account.Type.LIABILITY,
            "merchant": merchant,
        },
    )
    return acct
