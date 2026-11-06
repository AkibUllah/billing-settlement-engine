"""DCN1, the plain-text payment file of the fictional Demo Clearing Network.

One CSV record per row, LF line endings, no timestamps, so a batch always renders to the same bytes:

    H,DCN1,<originator>,<batch reference>,<value date YYYYMMDD>,<currency>
    D,<sequence>,<routing number>,<account number>,<account name>,<amount>,<line reference>
    T,<entry count>,<total amount>,<routing hash>

The routing hash is the sum of the routing numbers modulo 10^10, zero-padded to ten digits. Like
the total, it lets a receiver detect altered or dropped rows.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounts.models import (
    validate_account_name,
    validate_account_number,
    validate_routing_number,
)
from apps.common import ZERO

FORMAT = "DCN1"
HASH_MODULUS = 10**10
FIELD_COUNTS = {"H": 6, "D": 7, "T": 4}
_AMOUNT = re.compile(r"[0-9]{1,16}\.[0-9]{2}\Z")
_ORIGINATOR = re.compile(r"[A-Z0-9]{1,16}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


class BankFileError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    sequence: int
    routing_number: str
    account_number: str
    account_name: str
    amount: Decimal
    reference: str


@dataclass(frozen=True)
class BankFile:
    originator: str
    batch_reference: str
    value_date: date
    currency: str
    entries: tuple[Entry, ...]
    total: Decimal


def routing_hash(routing_numbers) -> str:
    return f"{sum(int(n) for n in routing_numbers) % HASH_MODULUS:010d}"


def render(
    *, originator: str, batch_reference: str, value_date: date, currency: str, entries
) -> str:
    entries = sorted(entries, key=lambda entry: entry.sequence)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["H", FORMAT, originator, batch_reference, f"{value_date:%Y%m%d}", currency])
    for e in entries:
        writer.writerow(
            [
                "D",
                e.sequence,
                e.routing_number,
                e.account_number,
                e.account_name,
                f"{e.amount:.2f}",
                e.reference,
            ]
        )
    total = sum((e.amount for e in entries), ZERO)
    hash_value = routing_hash(e.routing_number for e in entries)
    writer.writerow(["T", len(entries), f"{total:.2f}", hash_value])
    return buffer.getvalue()


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_entry(row: list[str], line_number: int) -> Entry:
    _, sequence, routing, account, name, amount, reference = row
    for validator, value in (
        (validate_routing_number, routing),
        (validate_account_number, account),
        (validate_account_name, name),
    ):
        try:
            validator(value)
        except ValidationError as exc:
            raise BankFileError(f"line {line_number}: {' '.join(exc.messages)}") from exc
    if not _AMOUNT.match(amount) or Decimal(amount) <= ZERO:
        raise BankFileError(f"line {line_number}: bad amount {amount!r}")
    if not sequence.isascii() or not sequence.isdigit():
        raise BankFileError(f"line {line_number}: bad sequence {sequence!r}")
    if not _REFERENCE.match(reference):
        raise BankFileError(f"line {line_number}: bad reference {reference!r}")
    return Entry(int(sequence), routing, account, name, Decimal(amount), reference)


def _parse_header(row: list[str]) -> tuple[str, str, date, str]:
    _, format_name, originator, reference, value_date, currency = row
    if format_name != FORMAT:
        raise BankFileError(f"unsupported format {format_name!r}")
    if not _ORIGINATOR.match(originator):
        raise BankFileError(f"bad originator {originator!r}")
    if not _REFERENCE.match(reference):
        raise BankFileError(f"bad batch reference {reference!r}")
    try:
        parsed_date = datetime.strptime(value_date, "%Y%m%d").date()
    except ValueError as exc:
        raise BankFileError(f"bad value date {value_date!r}") from exc
    return originator, reference, parsed_date, currency


def _check_trailer(row: list[str], entries: list[Entry]) -> Decimal:
    _, count, total, hash_value = row
    actual_total = sum((e.amount for e in entries), ZERO)
    if count != str(len(entries)):
        raise BankFileError(f"trailer count {count} does not match {len(entries)} entries")
    if total != f"{actual_total:.2f}":
        raise BankFileError(f"trailer total {total} does not match entries ({actual_total:.2f})")
    if hash_value != routing_hash(e.routing_number for e in entries):
        raise BankFileError("trailer routing hash does not match entries")
    return actual_total


def parse(content: str) -> BankFile:
    """Read a DCN1 file back, raising BankFileError on anything a receiver would reject."""
    rows = list(csv.reader(io.StringIO(content, newline="")))
    if len(rows) < 3:
        raise BankFileError("a file needs a header, at least one entry and a trailer")
    for number, row in enumerate(rows, start=1):
        kind = row[0] if row else ""
        if kind not in FIELD_COUNTS:
            raise BankFileError(f"line {number}: unknown record type {kind!r}")
        if len(row) != FIELD_COUNTS[kind]:
            raise BankFileError(f"line {number}: expected {FIELD_COUNTS[kind]} fields")
    if rows[0][0] != "H" or rows[-1][0] != "T" or any(r[0] != "D" for r in rows[1:-1]):
        raise BankFileError("records must be one header, entries, then one trailer")

    originator, reference, value_date, currency = _parse_header(rows[0])
    entries = [_parse_entry(row, number) for number, row in enumerate(rows[1:-1], start=2)]
    if [e.sequence for e in entries] != list(range(1, len(entries) + 1)):
        raise BankFileError("entry sequence must run from 1 without gaps")
    if len({e.reference for e in entries}) != len(entries):
        raise BankFileError("entry references must be unique")
    total = _check_trailer(rows[-1], entries)
    return BankFile(originator, reference, value_date, currency, tuple(entries), total)
