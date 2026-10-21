"""python manage.py run_settlement --date 2026-10-20 [--partner BKASH ...] [--approve]"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Partner
from apps.common import format_bdt
from apps.settlement.services import SettlementError, approve_batch, run_settlement


class Command(BaseCommand):
    help = "Create settlement batches for active partners up to a cutoff date (default: yesterday)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", dest="cutoff", help="Cutoff date YYYY-MM-DD (default yesterday)"
        )
        parser.add_argument(
            "--partner", action="append", default=[], help="Partner code; repeatable"
        )
        parser.add_argument(
            "--approve", action="store_true", help="Approve created batches immediately"
        )

    def handle(self, *args, **options):
        cutoff = (
            date.fromisoformat(options["cutoff"])
            if options["cutoff"]
            else date.today() - timedelta(days=1)
        )
        partners = None
        if options["partner"]:
            partners = list(
                Partner.objects.filter(code__in=[c.upper() for c in options["partner"]])
            )
            missing = set(c.upper() for c in options["partner"]) - {p.code for p in partners}
            if missing:
                raise CommandError(f"unknown partner code(s): {', '.join(sorted(missing))}")
        try:
            batches = run_settlement(cutoff, partners)
        except SettlementError as exc:
            raise CommandError(str(exc)) from exc
        if not batches:
            self.stdout.write(self.style.WARNING(f"No unsettled transactions up to {cutoff}."))
            return
        for batch in batches:
            if options["approve"]:
                approve_batch(batch)
            self.stdout.write(
                f"{batch.reference}: {batch.transaction_count} txns, "
                f"gross {format_bdt(batch.gross_amount)}, "
                f"refunds {format_bdt(batch.refund_amount)}, "
                f"commission {format_bdt(batch.fee_amount)}, "
                f"VAT {format_bdt(batch.vat_amount)} "
                f"-> net {format_bdt(batch.net_amount)} [{batch.status}]"
            )
        self.stdout.write(
            self.style.SUCCESS(f"Created {len(batches)} batch(es) for cutoff {cutoff}.")
        )
