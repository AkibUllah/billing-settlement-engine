from decimal import Decimal

import pytest

from apps.billing.models import FeeSchedule
from apps.billing.services import BillingError, calculate_fee
from apps.common import format_bdt, money

pytestmark = pytest.mark.django_db


def test_percentage_fee_with_vat_and_half_up_rounding(pct_schedule):
    fee = calculate_fee(pct_schedule, Decimal("1500"))
    assert fee.fee == Decimal("27.75")  # 1.85 %
    assert fee.vat == Decimal("4.16")  # 15 % of 27.75 = 4.1625 -> 4.16
    assert fee.total == Decimal("31.91")
    # 1.85% of 0.27 = 0.004995 -> 0.00 ; 1.85% of 0.30 = 0.00555 -> 0.01
    assert calculate_fee(pct_schedule, Decimal("0.27")).fee == Decimal("0.00")
    assert calculate_fee(pct_schedule, Decimal("0.30")).fee == Decimal("0.01")


def test_fixed_and_percentage_plus_fixed():
    fixed = FeeSchedule.objects.create(
        name="flat", fee_type=FeeSchedule.FeeType.FIXED, fixed_amount=Decimal("10")
    )
    assert calculate_fee(fixed, Decimal("99999")).fee == Decimal("10.00")
    combo = FeeSchedule.objects.create(
        name="combo",
        fee_type=FeeSchedule.FeeType.PERCENTAGE_PLUS_FIXED,
        percentage=Decimal("1"),
        fixed_amount=Decimal("10"),
    )
    breakdown = calculate_fee(combo, Decimal("2500"))
    assert breakdown.fee == Decimal("35.00")
    assert breakdown.detail == "1.0000% + 10.00"


def test_min_and_max_fee_caps(merchant_schedule):
    floored = calculate_fee(merchant_schedule, Decimal("100"))  # 2% = 2.00 < min 5.00
    assert floored.fee == Decimal("5.00") and "floored" in floored.detail
    capped_schedule = FeeSchedule.objects.create(
        name="capped",
        fee_type=FeeSchedule.FeeType.PERCENTAGE,
        percentage=Decimal("2"),
        max_fee=Decimal("100"),
    )
    capped = calculate_fee(capped_schedule, Decimal("100000"))
    assert capped.fee == Decimal("100.00") and "capped" in capped.detail
    assert capped.vat == Decimal("15.00")


def test_tiered_slab_uses_rate_of_containing_tier(tiered_slab):
    assert calculate_fee(tiered_slab, Decimal("999.99")).fee == Decimal("25.00")  # 2.5 %
    assert calculate_fee(tiered_slab, Decimal("1000")).fee == Decimal(
        "20.00"
    )  # boundary -> next tier, 2 %
    assert calculate_fee(tiered_slab, Decimal("25000")).fee == Decimal("400.00")  # 1.6 %


def test_tiered_marginal_charges_each_band(tiered_marginal):
    # 5000 @ 2.2% = 110 ; 45000 @ 1.8% = 810 ; 10000 @ 1.4% = 140  -> 1060
    breakdown = calculate_fee(tiered_marginal, Decimal("60000"))
    assert breakdown.fee == Decimal("1060.00")
    assert breakdown.detail.startswith(
        "marginal 5000.00@2.2000% + 45000.00@1.8000% + 10000.00@1.4000%"
    )
    assert calculate_fee(tiered_marginal, Decimal("3000")).fee == Decimal("66.00")


def test_tiered_schedule_without_tiers_and_negative_amount_raise():
    empty = FeeSchedule.objects.create(name="empty", fee_type=FeeSchedule.FeeType.TIERED)
    with pytest.raises(BillingError, match="no tiers"):
        calculate_fee(empty, Decimal("10"))
    with pytest.raises(BillingError, match="negative"):
        calculate_fee(empty, Decimal("-1"))


def test_no_schedule_means_zero_fee():
    breakdown = calculate_fee(None, Decimal("500"))
    assert breakdown.fee == breakdown.vat == Decimal("0.00")
    assert breakdown.schedule == "none"


def test_money_helpers():
    assert money("1.005") == Decimal("1.01")
    assert money(Decimal("2.675")) == Decimal("2.68")
    assert format_bdt(Decimal("1234567.89")) == "৳12,34,567.89"
    assert format_bdt(Decimal("999")) == "৳999.00"
    assert format_bdt(Decimal("-150000")) == "-৳1,50,000.00"
