"""python manage.py run_payouts [--date 2026-11-08] [--minimum 500] [--merchant CODE ...]
[--approve] [--export-dir DIR]"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Merchant
from apps.common import format_bdt
from apps.payouts.services import (
    NothingToPay,
    PayoutError,
    approve_batch,
    bank_file_content,
    bank_file_name,
    create_batch,
    export_batch,
)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"bad --date {value!r}, expected YYYY-MM-DD") from exc


def _parse_minimum(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise CommandError(f"bad --minimum {value!r}") from exc


class Command(BaseCommand):
    help = (
        "Draft a payout batch from merchant payable balances, optionally approving and exporting."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", dest="value_date", help="Value date YYYY-MM-DD (default today)"
        )
        parser.add_argument("--minimum", default="0", help="Leave smaller balances for a later run")
        parser.add_argument(
            "--merchant", action="append", default=[], help="Merchant code; repeatable"
        )
        parser.add_argument("--approve", action="store_true", help="Post the journals immediately")
        parser.add_argument(
            "--export-dir", help="Write the DCN1 bank file to this directory (implies --approve)"
        )

    def handle(self, *args, **options):
        value_date = _parse_date(options["value_date"]) if options["value_date"] else None
        minimum = _parse_minimum(options["minimum"])
        codes = {code.upper() for code in options["merchant"]}
        merchants = None
        if codes:
            merchants = list(Merchant.objects.filter(code__in=codes))
            if missing := codes - {m.code for m in merchants}:
                raise CommandError(f"unknown merchant code(s): {', '.join(sorted(missing))}")
        export_dir = Path(options["export_dir"]) if options["export_dir"] else None
        if export_dir is not None and not export_dir.is_dir():
            raise CommandError(f"{export_dir} is not a directory")

        try:
            batch = create_batch(value_date, minimum_amount=minimum, merchants=merchants)
        except NothingToPay as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return
        except PayoutError as exc:
            raise CommandError(str(exc)) from exc

        try:
            if options["approve"] or export_dir is not None:
                batch = approve_batch(batch)
            if export_dir is not None:
                batch = export_batch(batch)
        except PayoutError as exc:
            raise CommandError(f"{batch.reference} left as {batch.status}: {exc}") from exc

        for line in batch.lines.select_related("merchant"):
            self.stdout.write(f"{line.reference}: {line.merchant.code} {format_bdt(line.amount)}")
        for skipped in batch.skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"skipped {skipped['merchant']} ({skipped['balance']}): {skipped['reason']}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{batch.reference}: {batch.line_count} line(s), "
                f"{format_bdt(batch.total_amount)} [{batch.status}]"
            )
        )
        if export_dir is not None:
            path = export_dir / bank_file_name(batch)
            path.write_text(bank_file_content(batch), encoding="utf-8", newline="")
            self.stdout.write(f"Wrote {path} (sha256 {batch.bank_file_sha256})")
