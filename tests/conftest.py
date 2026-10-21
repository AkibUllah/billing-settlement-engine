from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import Merchant, Partner
from apps.billing.models import FeeSchedule, FeeTier, Transaction
from apps.billing.services import record_transaction
from apps.ledger.services import ensure_chart_of_accounts


@pytest.fixture
def chart(db):
    return ensure_chart_of_accounts()


@pytest.fixture
def pct_schedule(db) -> FeeSchedule:
    return FeeSchedule.objects.create(
        name="bKash commission", fee_type=FeeSchedule.FeeType.PERCENTAGE, percentage=Decimal("1.85")
    )


@pytest.fixture
def merchant_schedule(db) -> FeeSchedule:
    return FeeSchedule.objects.create(
        name="Standard 2%",
        fee_type=FeeSchedule.FeeType.PERCENTAGE,
        percentage=Decimal("2.00"),
        min_fee=Decimal("5.00"),
    )


@pytest.fixture
def tiered_slab(db) -> FeeSchedule:
    schedule = FeeSchedule.objects.create(
        name="Card acquiring",
        fee_type=FeeSchedule.FeeType.TIERED,
        tier_mode=FeeSchedule.TierMode.SLAB,
    )
    FeeTier.objects.bulk_create(
        [
            FeeTier(schedule=schedule, lower_bound=0, upper_bound=1000, percentage=Decimal("2.50")),
            FeeTier(
                schedule=schedule, lower_bound=1000, upper_bound=10000, percentage=Decimal("2.00")
            ),
            FeeTier(
                schedule=schedule, lower_bound=10000, upper_bound=None, percentage=Decimal("1.60")
            ),
        ]
    )
    return schedule


@pytest.fixture
def tiered_marginal(db) -> FeeSchedule:
    schedule = FeeSchedule.objects.create(
        name="Volume tiered",
        fee_type=FeeSchedule.FeeType.TIERED,
        tier_mode=FeeSchedule.TierMode.MARGINAL,
        fixed_amount=Decimal("0"),
    )
    FeeTier.objects.bulk_create(
        [
            FeeTier(schedule=schedule, lower_bound=0, upper_bound=5000, percentage=Decimal("2.20")),
            FeeTier(
                schedule=schedule, lower_bound=5000, upper_bound=50000, percentage=Decimal("1.80")
            ),
            FeeTier(
                schedule=schedule, lower_bound=50000, upper_bound=None, percentage=Decimal("1.40")
            ),
        ]
    )
    return schedule


@pytest.fixture
def bkash(db, pct_schedule) -> Partner:
    return Partner.objects.create(code="BKASH", name="bKash", fee_schedule=pct_schedule)


@pytest.fixture
def nagad(db) -> Partner:
    schedule = FeeSchedule.objects.create(
        name="Nagad commission", fee_type=FeeSchedule.FeeType.PERCENTAGE, percentage=Decimal("1.50")
    )
    return Partner.objects.create(code="NAGAD", name="Nagad", fee_schedule=schedule)


@pytest.fixture
def dhaka_mart(db, merchant_schedule) -> Merchant:
    return Merchant.objects.create(
        code="DHAKAMART",
        name="Dhaka Mart",
        legal_name="Dhaka Mart Ltd.",
        address="Gulshan-1, Dhaka 1212",
        bin_number="001234567-0101",
        fee_schedule=merchant_schedule,
    )


@pytest.fixture
def ctg_traders(db, merchant_schedule) -> Merchant:
    return Merchant.objects.create(
        code="CTGTRADERS", name="Chittagong Traders", fee_schedule=merchant_schedule
    )


def make_txn(partner, merchant, amount, *, ref, type=Transaction.Type.PAYMENT, days_ago=1):
    return record_transaction(
        external_ref=ref,
        merchant=merchant,
        partner=partner,
        amount=Decimal(str(amount)),
        type=type,
        occurred_at=timezone.now() - timedelta(days=days_ago),
    )


@pytest.fixture
def api(db):
    user = get_user_model().objects.create_user("ops", "ops@example.com", "pass12345")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    client.user = user
    return client
