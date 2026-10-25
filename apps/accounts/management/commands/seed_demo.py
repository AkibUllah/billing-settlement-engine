"""python manage.py seed_demo  — partners, merchants, fee schedules and a week of transactions."""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.accounts.models import Merchant, Partner
from apps.billing.models import FeeSchedule, FeeTier, Transaction
from apps.billing.services import record_transaction
from apps.ledger.services import ensure_chart_of_accounts

PARTNERS = [
    ("BKASH", "bKash", Partner.Type.MFS, "bKash merchant commission"),
    ("NAGAD", "Nagad", Partner.Type.MFS, "Nagad merchant commission"),
    ("ROCKET", "Rocket (DBBL)", Partner.Type.MFS, "Rocket commission"),
    ("DEMOBANK", "Demo Card Bank", Partner.Type.BANK, "Card acquiring (tiered)"),
]

MERCHANTS = [
    ("DHAKAMART", "Dhaka Mart", "Dhaka Mart Ltd.", "Gulshan-1, Dhaka 1212", "Standard 2%"),
    (
        "CTGTRADERS",
        "Chittagong Traders",
        "Chittagong Traders & Co.",
        "Agrabad C/A, Chattogram 4100",
        "Volume tiered",
    ),
    (
        "SYLHETAGRO",
        "Sylhet Agro",
        "Sylhet Agro Supplies",
        "Zindabazar, Sylhet 3100",
        "Flat ৳10 + 1%",
    ),
]


class Command(BaseCommand):
    help = "Seed partners, merchants, fee schedules, a demo API user and ~7 days of transactions."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--per-day", type=int, default=12)
        parser.add_argument("--seed", type=int, default=2026)

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(options["seed"])
        ensure_chart_of_accounts()

        schedules = {
            "bKash merchant commission": self._schedule(
                "bKash merchant commission", FeeSchedule.FeeType.PERCENTAGE, percentage="1.85"
            ),
            "Nagad merchant commission": self._schedule(
                "Nagad merchant commission", FeeSchedule.FeeType.PERCENTAGE, percentage="1.50"
            ),
            "Rocket commission": self._schedule(
                "Rocket commission",
                FeeSchedule.FeeType.PERCENTAGE_PLUS_FIXED,
                percentage="1.00",
                fixed_amount="2.00",
            ),
            "Standard 2%": self._schedule(
                "Standard 2%", FeeSchedule.FeeType.PERCENTAGE, percentage="2.00", min_fee="5.00"
            ),
            "Flat ৳10 + 1%": self._schedule(
                "Flat ৳10 + 1%",
                FeeSchedule.FeeType.PERCENTAGE_PLUS_FIXED,
                percentage="1.00",
                fixed_amount="10.00",
            ),
        }
        tiered = self._schedule(
            "Card acquiring (tiered)",
            FeeSchedule.FeeType.TIERED,
            tier_mode=FeeSchedule.TierMode.SLAB,
        )
        if not tiered.tiers.exists():
            FeeTier.objects.bulk_create(
                [
                    FeeTier(schedule=tiered, lower_bound=0, upper_bound=1000, percentage="2.50"),
                    FeeTier(
                        schedule=tiered, lower_bound=1000, upper_bound=10000, percentage="2.00"
                    ),
                    FeeTier(
                        schedule=tiered, lower_bound=10000, upper_bound=None, percentage="1.60"
                    ),
                ]
            )
        schedules["Card acquiring (tiered)"] = tiered
        volume = self._schedule(
            "Volume tiered", FeeSchedule.FeeType.TIERED, tier_mode=FeeSchedule.TierMode.MARGINAL
        )
        if not volume.tiers.exists():
            FeeTier.objects.bulk_create(
                [
                    FeeTier(schedule=volume, lower_bound=0, upper_bound=5000, percentage="2.20"),
                    FeeTier(
                        schedule=volume, lower_bound=5000, upper_bound=50000, percentage="1.80"
                    ),
                    FeeTier(
                        schedule=volume, lower_bound=50000, upper_bound=None, percentage="1.40"
                    ),
                ]
            )
        schedules["Volume tiered"] = volume

        partners = []
        for code, name, type_, schedule_name in PARTNERS:
            partner, _ = Partner.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "type": type_,
                    "fee_schedule": schedules[schedule_name],
                    "settlement_account": f"{code.lower()}-settlement-01",
                    "contact_email": f"settlement@{code.lower()}.example",
                },
            )
            partners.append(partner)

        merchants = []
        for code, name, legal, address, schedule_name in MERCHANTS:
            merchant, _ = Merchant.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "legal_name": legal,
                    "address": address,
                    "bin_number": f"00{rng.randint(1000000, 9999999)}-0{rng.randint(100, 999)}",
                    "contact_email": f"finance@{code.lower()}.example",
                    "fee_schedule": schedules[schedule_name],
                },
            )
            merchants.append(merchant)

        created = 0
        now = timezone.now()
        for day in range(options["days"], 0, -1):
            for n in range(options["per_day"]):
                ref = f"DEMO{day:02d}{n:03d}{rng.randint(100, 999)}"
                if Transaction.objects.filter(external_ref=ref).exists():
                    continue
                partner = rng.choice(partners)
                merchant = rng.choice(merchants)
                is_refund = rng.random() < 0.08
                amount = Decimal(
                    rng.choice([150, 250, 499, 850, 1200, 1500, 2750, 4999, 12000, 25000])
                )
                record_transaction(
                    external_ref=ref,
                    merchant=merchant,
                    partner=partner,
                    amount=amount if not is_refund else amount / 2,
                    type=Transaction.Type.REFUND if is_refund else Transaction.Type.PAYMENT,
                    occurred_at=now
                    - timedelta(days=day, hours=rng.randint(0, 23), minutes=rng.randint(0, 59)),
                    customer_msisdn=f"017{rng.randint(10000000, 99999999)}",
                )
                created += 1

        user, made = get_user_model().objects.get_or_create(
            username="demo",
            defaults={"is_staff": True, "is_superuser": True, "email": "demo@example.com"},
        )
        if made:
            user.set_password("demo12345")
            user.save()
        token, _ = Token.objects.get_or_create(user=user)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(partners)} partners, {len(merchants)} merchants, "
                f"{len(schedules)} fee schedules, {created} new transactions."
            )
        )
        self.stdout.write(f"Admin login: demo / demo12345   API token: {token.key}")

    @staticmethod
    def _schedule(name, fee_type, **fields):
        defaults = {"fee_type": fee_type}
        for key, value in fields.items():
            defaults[key] = (
                Decimal(value) if isinstance(value, str) and key != "tier_mode" else value
            )
        schedule, _ = FeeSchedule.objects.update_or_create(name=name, defaults=defaults)
        return schedule
