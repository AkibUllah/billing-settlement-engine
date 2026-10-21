from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import Invoice, Transaction
from apps.billing.services import (
    BillingError,
    generate_invoice,
    issue_invoice,
    render_invoice_csv,
    render_invoice_text,
)
from apps.ledger.models import Account
from tests.conftest import make_txn

pytestmark = pytest.mark.django_db


@pytest.fixture
def period():
    today = timezone.localdate()
    return today - timedelta(days=10), today


def test_generate_invoice_aggregates_fees_and_vat(chart, bkash, dhaka_mart, period):
    make_txn(bkash, dhaka_mart, "1500", ref="A", days_ago=2)  # fee 30.00 vat 4.50
    make_txn(bkash, dhaka_mart, "100", ref="B", days_ago=3)  # fee 5.00 (min) vat 0.75
    make_txn(bkash, dhaka_mart, "700", ref="R", type=Transaction.Type.REFUND, days_ago=2)  # ignored
    make_txn(bkash, dhaka_mart, "999", ref="OLD", days_ago=40)  # outside period

    invoice = generate_invoice(dhaka_mart, *period)
    assert invoice.number.startswith(f"INV-{period[1]:%Y%m}-DHAKAMART-")
    assert invoice.subtotal == Decimal("35.00")
    assert invoice.vat_amount == Decimal("5.25")
    assert invoice.total == Decimal("40.25")
    assert invoice.status == Invoice.Status.DRAFT
    lines = list(invoice.lines.all())
    assert lines[0].description.startswith("Processing fees on 2 payments totalling ৳1,600.00")
    assert lines[0].quantity == 2 and lines[0].amount == Decimal("35.00")
    assert lines[1].is_vat and lines[1].amount == Decimal("5.25")
    assert Transaction.objects.filter(invoice=invoice).count() == 2

    with pytest.raises(BillingError, match="already exists"):
        generate_invoice(dhaka_mart, *period)
    with pytest.raises(BillingError, match="before"):
        generate_invoice(dhaka_mart, period[1], period[0])


def test_issue_invoice_posts_revenue_and_vat(chart, bkash, dhaka_mart, period):
    make_txn(bkash, dhaka_mart, "1500", ref="A", days_ago=2)
    invoice = issue_invoice(generate_invoice(dhaka_mart, *period))
    assert invoice.status == Invoice.Status.ISSUED and invoice.journal is not None
    assert chart["4000"].balance() == Decimal("30.00")
    assert chart["2100"].balance() == Decimal("4.50")
    assert Account.objects.get(code="2000-DHAKAMART").balance() == Decimal(
        "-34.50"
    )  # deducted from payable
    with pytest.raises(BillingError, match="not draft"):
        issue_invoice(invoice)


def test_empty_period_invoice_has_no_lines_and_no_journal(chart, dhaka_mart, period):
    invoice = issue_invoice(generate_invoice(dhaka_mart, *period))
    assert invoice.total == Decimal("0.00")
    assert invoice.lines.count() == 0
    assert invoice.journal is None


def test_text_and_csv_rendering(chart, bkash, dhaka_mart, period):
    make_txn(bkash, dhaka_mart, "1500", ref="A", days_ago=2)
    invoice = generate_invoice(dhaka_mart, *period)
    text = render_invoice_text(invoice)
    assert f"INVOICE {invoice.number}" in text
    assert "Dhaka Mart Ltd." in text and "BIN: 001234567-0101" in text
    assert "TOTAL" in text and "৳34.50" in text
    csv_rows = render_invoice_csv(invoice).strip().splitlines()
    assert csv_rows[0] == "invoice,description,quantity,unit_amount,amount,is_vat"
    assert csv_rows[-1].endswith("TOTAL,,,34.50,")
