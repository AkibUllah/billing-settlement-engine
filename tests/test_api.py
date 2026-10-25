import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Transaction
from apps.settlement.models import SettlementBatch
from tests.conftest import make_txn

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()


def test_health_is_public_and_api_requires_token(db):
    anon = APIClient()
    assert anon.get("/api/health/").json()["status"] == "ok"
    assert anon.get("/api/partners/").status_code == 401
    assert (
        anon.get("/api/schema/").status_code == 401 or anon.get("/api/schema/").status_code == 200
    )


def test_token_login_and_openapi_schema(api, django_user_model):
    anon = APIClient()
    r = anon.post("/api/auth/token/", {"username": "ops", "password": "pass12345"})
    assert r.status_code == 200 and "token" in r.json()
    schema = api.get("/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/settlements/batches/{id}/approve/" in paths
    assert "/api/invoices/generate/" in paths
    assert "/api/ledger/trial-balance/" in paths


def test_partner_and_merchant_crud_with_filters(api, pct_schedule):
    r = api.post(
        "/api/partners/",
        {"code": "BKASH", "name": "bKash", "type": "mfs", "fee_schedule": pct_schedule.id},
    )
    assert r.status_code == 201 and r.json()["fee_schedule_name"] == "bKash commission"
    api.post("/api/partners/", {"code": "DEMOBANK", "name": "Demo Card Bank", "type": "bank"})
    assert api.get("/api/partners/", {"type": "bank"}).json()["count"] == 1
    assert api.get("/api/partners/", {"search": "bka"}).json()["count"] == 1

    r = api.post(
        "/api/merchants/",
        {"code": "DHAKAMART", "name": "Dhaka Mart", "fee_schedule": pct_schedule.id},
    )
    assert r.status_code == 201
    r = api.patch(f"/api/merchants/{r.json()['id']}/", {"is_active": False})
    assert r.json()["is_active"] is False


def test_fee_schedule_with_tiers_and_quote(api):
    r = api.post(
        "/api/fee-schedules/",
        {
            "name": "Card tiered",
            "fee_type": "tiered",
            "tier_mode": "slab",
            "tiers": [
                {"lower_bound": "0", "upper_bound": "1000", "percentage": "2.5"},
                {"lower_bound": "1000", "upper_bound": None, "percentage": "2.0"},
            ],
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    schedule_id = r.json()["id"]
    assert len(r.json()["tiers"]) == 2
    quote = api.post(f"/api/fee-schedules/{schedule_id}/quote/", {"amount": "5000"}).json()
    assert quote == {
        "schedule": "Card tiered",
        "amount": "5000.00",
        "fee": "100.00",
        "vat": "15.00",
        "total": "115.00",
        "detail": quote["detail"],
    }
    r = api.put(
        f"/api/fee-schedules/{schedule_id}/",
        {
            "name": "Card tiered",
            "fee_type": "tiered",
            "tier_mode": "slab",
            "tiers": [{"lower_bound": "0", "percentage": "1"}],
        },
        format="json",
    )
    assert len(r.json()["tiers"]) == 1


def test_transaction_create_computes_fees_and_is_immutable(api, bkash, dhaka_mart):
    r = api.post(
        "/api/transactions/",
        {
            "external_ref": "9HK7A2B3C4",
            "merchant": dhaka_mart.id,
            "partner": bkash.id,
            "type": "payment",
            "amount": "1500.00",
            "occurred_at": timezone.now().isoformat(),
            "customer_msisdn": "01711000000",
        },
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["partner_fee"] == "27.75" and body["merchant_fee"] == "30.00"
    assert body["status"] == "unsettled" and body["partner_code"] == "BKASH"

    dup = api.post(
        "/api/transactions/",
        {
            "external_ref": "9HK7A2B3C4",
            "merchant": dhaka_mart.id,
            "partner": bkash.id,
            "amount": "1",
            "occurred_at": timezone.now().isoformat(),
        },
    )
    assert dup.status_code == 400
    neg = api.post(
        "/api/transactions/",
        {
            "external_ref": "X",
            "merchant": dhaka_mart.id,
            "partner": bkash.id,
            "amount": "-5",
            "occurred_at": timezone.now().isoformat(),
        },
    )
    assert neg.status_code == 400
    assert api.patch(f"/api/transactions/{body['id']}/", {"amount": "1"}).status_code == 405
    assert api.get("/api/transactions/", {"settlement_batch__isnull": "true"}).json()["count"] == 1


def test_settlement_flow_through_api(api, chart, bkash, nagad, dhaka_mart):
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, dhaka_mart, "300", ref="R", type=Transaction.Type.REFUND)
    make_txn(nagad, dhaka_mart, "500", ref="N")

    run = api.post(
        "/api/settlements/batches/run/",
        {"cutoff_date": YESTERDAY.isoformat(), "partners": [bkash.id]},
        format="json",
    )
    assert run.status_code == 201, run.content
    assert len(run.json()) == 1
    batch = run.json()[0]
    assert batch["partner_code"] == "BKASH"
    assert Decimal(batch["net_amount"]) == Decimal("1000") - Decimal("300") - Decimal(
        "18.50"
    ) - Decimal("2.78")
    assert Transaction.objects.get(external_ref="N").status == "unsettled"

    export = api.get(f"/api/settlements/batches/{batch['id']}/export/")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")

    report_csv = io.BytesIO(b"external_ref,type,amount\nA,payment,1000\nR,refund,300\n")
    report_csv.name = "bkash-report.csv"
    rec = api.post(
        f"/api/settlements/batches/{batch['id']}/reconcile/",
        {"file": report_csv},
        format="multipart",
    )
    assert rec.status_code == 201, rec.content
    assert rec.json()["is_clean"] is True and rec.json()["matched"] == 2
    assert api.get("/api/settlements/reconciliations/", {"is_clean": "true"}).json()["count"] == 1

    paid_early = api.post(f"/api/settlements/batches/{batch['id']}/mark-paid/", {})
    assert paid_early.status_code == 409

    approved = api.post(f"/api/settlements/batches/{batch['id']}/approve/")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by_username"] == "ops"
    assert api.post(f"/api/settlements/batches/{batch['id']}/approve/").status_code == 409

    paid = api.post(
        f"/api/settlements/batches/{batch['id']}/mark-paid/", {"payment_reference": "BEFTN-1"}
    )
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    assert paid.json()["payment_reference"] == "BEFTN-1"

    assert api.get("/api/settlements/batches/", {"status": "paid"}).json()["count"] == 1
    assert SettlementBatch.objects.get(pk=batch["id"]).payment_journal is not None

    journals = api.get("/api/ledger/journals/", {"source": "settlement:paid"}).json()
    assert journals["count"] == 1
    assert {p["account_code"] for p in journals["results"][0]["postings"]} == {"1000", "1100-BKASH"}
    tb = api.get("/api/ledger/trial-balance/").json()
    assert tb["balanced"] is True and tb["total_debit"] == tb["total_credit"]
    accounts = api.get("/api/ledger/accounts/", {"type": "asset"}).json()["results"]
    assert {a["code"]: a["balance"] for a in accounts}["1000"] == "678.72"


def test_invoice_flow_through_api(api, chart, bkash, dhaka_mart):
    make_txn(bkash, dhaka_mart, "1500", ref="A", days_ago=2)
    today = timezone.localdate()
    r = api.post(
        "/api/invoices/generate/",
        {
            "merchant": dhaka_mart.id,
            "period_start": (today - timedelta(days=7)).isoformat(),
            "period_end": today.isoformat(),
        },
    )
    assert r.status_code == 201, r.content
    invoice = r.json()
    assert invoice["total"] == "34.50" and len(invoice["lines"]) == 2
    again = api.post(
        "/api/invoices/generate/",
        {
            "merchant": dhaka_mart.id,
            "period_start": (today - timedelta(days=7)).isoformat(),
            "period_end": today.isoformat(),
        },
    )
    assert again.status_code == 400

    text = api.get(f"/api/invoices/{invoice['id']}/text/")
    assert text.status_code == 200 and b"INVOICE INV-" in text.content
    csv_export = api.get(f"/api/invoices/{invoice['id']}/export/")
    assert csv_export["Content-Disposition"].endswith('.csv"')

    issued = api.post(f"/api/invoices/{invoice['id']}/issue/")
    assert issued.status_code == 200 and issued.json()["status"] == "issued"
    assert api.post(f"/api/invoices/{invoice['id']}/issue/").status_code == 409
    assert api.get("/api/invoices/", {"status": "issued"}).json()["count"] == 1
