"""Shared money helpers. All amounts are Decimal with two places, rounded half-up."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | str) -> Decimal:
    """Quantize to 0.01 with ROUND_HALF_UP (the convention BD banks use for taka/paisa)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def percent_of(amount: Decimal, rate_percent: Decimal) -> Decimal:
    return money(Decimal(amount) * Decimal(rate_percent) / Decimal(100))


def format_bdt(amount: Decimal) -> str:
    """Bangladeshi digit grouping: ৳12,34,567.89 (lakh/crore)."""
    amount = money(amount)
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}৳{whole}.{frac}"
