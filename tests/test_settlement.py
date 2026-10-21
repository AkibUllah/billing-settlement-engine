from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import Transaction
from apps.ledger.models import Account
from apps.ledger.services import trial_balance
from apps.settlement.models import SettlementBatch
from apps.settlement.services import (
    InvalidTransition,
    SettlementError,
    approve_batch,
    create_batch,
    export_csv,
    mark_paid,
    reconcile,
    run_settlement,
)
from tests.conftest import make_txn

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()


def test_record_transaction_freezes_fees_and_refunds_have_none(bkash, dhaka_mart):
    payment = make_txn(bkash, dhaka_mart, "1500", ref="TRX1")
    assert payment.partner_fee == Decimal("27.75") and payment.partner_vat == Decimal("4.16")
    assert payment.merchant_fee == Decimal("30.00") and payment.merchant_vat == Decimal("4.50")
    refund = make_txn(bkash, dhaka_mart, "500", ref="TRX2", type=Transaction.Type.REFUND)
    assert refund.partner_fee == refund.merchant_fee == Decimal("0.00")
    assert refund.signed_amount == Decimal("-500.00")
    bkash.fee_schedule.percentage = Decimal("5")
    bkash.fee_schedule.save()
    payment.refresh_from_db()
    assert payment.partner_fee == Decimal("27.75")  # later schedule change does not rewrite history


def test_batch_nets_payments_refunds_commission_and_vat(bkash, dhaka_mart, ctg_traders):
    make_txn(bkash, dhaka_mart, "1000", ref="A")  # fee 18.50 vat 2.78
    make_txn(bkash, ctg_traders, "2000", ref="B")  # fee 37.00 vat 5.55
    make_txn(bkash, dhaka_mart, "300", ref="C", type=Transaction.Type.REFUND)
    make_txn(bkash, dhaka_mart, "999", ref="FUTURE", days_ago=-2)  # after cutoff

    batch = create_batch(bkash, YESTERDAY)
    assert batch.reference == f"SET-BKASH-{YESTERDAY:%Y%m%d}"
    assert batch.transaction_count == 3
    assert batch.gross_amount == Decimal("3000.00")
    assert batch.refund_amount == Decimal("300.00")
    assert batch.fee_amount == Decimal("55.50")
    assert batch.vat_amount == Decimal("8.33")
    assert batch.net_amount == Decimal("2636.17")
    assert batch.status == SettlementBatch.Status.PENDING
    assert Transaction.objects.filter(settlement_batch=batch, status="settled").count() == 3
    assert Transaction.objects.get(external_ref="FUTURE").status == "unsettled"


def test_run_settlement_groups_per_partner_and_is_idempotent(bkash, nagad, dhaka_mart):
    make_txn(bkash, dhaka_mart, "100", ref="B1")
    make_txn(nagad, dhaka_mart, "200", ref="N1")
    batches = run_settlement(YESTERDAY)
    assert sorted(b.partner.code for b in batches) == ["BKASH", "NAGAD"]
    assert run_settlement(YESTERDAY) == []  # nothing left, no duplicate batches
    assert SettlementBatch.objects.count() == 2
    with pytest.raises(SettlementError, match="already exists"):
        create_batch(bkash, YESTERDAY)
    assert create_batch(bkash, YESTERDAY + timedelta(days=1)) is None  # nothing unsettled


def test_workflow_pending_approved_paid_posts_ledger(chart, bkash, dhaka_mart, ctg_traders):
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    make_txn(bkash, dhaka_mart, "300", ref="C", type=Transaction.Type.REFUND)
    batch = create_batch(bkash, YESTERDAY)

    with pytest.raises(InvalidTransition):
        mark_paid(batch)  # cannot skip approval

    approve_batch(batch)
    assert batch.status == SettlementBatch.Status.APPROVED
    assert batch.approved_at is not None
    receivable = Account.objects.get(code="1100-BKASH")
    assert receivable.balance() == Decimal("2636.17")
    assert chart["5000"].balance() == Decimal("55.50")
    assert chart["1300"].balance() == Decimal("8.33")
    assert Account.objects.get(code="2000-DHAKAMART").balance() == Decimal("700.00")
    assert Account.objects.get(code="2000-CTGTRADERS").balance() == Decimal("2000.00")
    assert trial_balance()["balanced"] is True

    with pytest.raises(InvalidTransition):
        approve_batch(batch)

    mark_paid(batch, payment_reference="BEFTN-20261021-001")
    assert batch.status == SettlementBatch.Status.PAID
    assert batch.payment_reference == "BEFTN-20261021-001"
    assert receivable.balance() == Decimal("0.00")
    assert chart["1000"].balance() == Decimal("2636.17")
    assert trial_balance()["balanced"] is True
    with pytest.raises(InvalidTransition):
        mark_paid(batch)


def test_export_csv_lists_transactions_and_net_row(bkash, dhaka_mart):
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, dhaka_mart, "250", ref="R", type=Transaction.Type.REFUND)
    batch = create_batch(bkash, YESTERDAY)
    rows = export_csv(batch).strip().splitlines()
    assert rows[0].startswith("batch,external_ref,type,merchant,amount")
    assert len(rows) == 4
    assert rows[-1].split(",")[1] == "NET"
    assert rows[-1].split(",")[4] == str(batch.net_amount)


def test_reconcile_clean_report(bkash, dhaka_mart):
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, dhaka_mart, "250", ref="R", type=Transaction.Type.REFUND)
    batch = create_batch(bkash, YESTERDAY)
    report = reconcile(
        batch, "external_ref,type,amount\nA,payment,1000.00\nR,refund,250\n", "bkash.csv"
    )
    assert report.is_clean and report.matched == 2
    assert report.partner_total == report.ledger_total == Decimal("750.00")
    batch.refresh_from_db()
    assert batch.reconciliation_status == "clean"


def test_reconcile_reports_mismatches_and_missing_rows(bkash, dhaka_mart):
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, dhaka_mart, "2000", ref="B")
    batch = create_batch(bkash, YESTERDAY)
    report = reconcile(
        batch,
        "External_Ref,Type,Amount\nA,payment,1000\nB,payment,1999.50\nGHOST,payment,5\n",
    )
    assert not report.is_clean
    assert report.matched == 1
    assert report.amount_mismatches == [
        {
            "external_ref": "B",
            "ledger_amount": "2000.00",
            "partner_amount": "1999.50",
            "ledger_type": "payment",
            "partner_type": "payment",
        }
    ]
    assert report.missing_in_ledger == ["GHOST"]
    assert report.missing_in_partner == []
    assert report.partner_total == Decimal("3004.50") and report.ledger_total == Decimal("3000.00")
    batch.refresh_from_db()
    assert batch.reconciliation_status == "discrepancy"

    second = reconcile(batch, "external_ref,type,amount\nA,payment,1000\n")
    assert second.missing_in_partner == ["B"]
    assert batch.reconciliations.count() == 2


def test_reconcile_rejects_bad_files_and_paid_batches(bkash, dhaka_mart):
    make_txn(bkash, dhaka_mart, "10", ref="A")
    batch = create_batch(bkash, YESTERDAY)
    with pytest.raises(SettlementError, match="needs columns"):
        reconcile(batch, "ref,amt\nA,10\n")
    with pytest.raises(SettlementError, match="bad amount"):
        reconcile(batch, "external_ref,type,amount\nA,payment,ten\n")
    approve_batch(batch)
    mark_paid(batch)
    with pytest.raises(SettlementError, match="closed"):
        reconcile(batch, "external_ref,type,amount\nA,payment,10\n")


def test_management_commands(bkash, nagad, dhaka_mart, capsys):
    from django.core.management import call_command

    call_command("seed_demo", days=2, per_day=5)
    out = capsys.readouterr().out
    assert "Seeded 4 partners, 3 merchants" in out and "API token:" in out
    assert Transaction.objects.count() == 10

    call_command("run_settlement", "--date", date.today().isoformat())
    out = capsys.readouterr().out
    assert "SET-" in out and "-> net" in out
    assert SettlementBatch.objects.count() >= 1
    assert Transaction.objects.filter(status="unsettled").count() == 0

    call_command("run_settlement", "--date", date.today().isoformat())
    assert "No unsettled transactions" in capsys.readouterr().out

    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="unknown partner"):
        call_command("run_settlement", "--partner", "NOPE")
