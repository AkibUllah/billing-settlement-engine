from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.services import generate_invoice, issue_invoice
from apps.ledger import services as ledger
from apps.payouts import bank_file
from apps.payouts.models import PayoutBatch
from apps.settlement.services import approve_batch as approve_settlement
from apps.settlement.services import create_batch as create_settlement
from tests.conftest import make_txn, set_bank_details

pytestmark = pytest.mark.django_db

YESTERDAY = (timezone.localtime() - timedelta(days=1)).date()
BATCHES = "/api/payouts/batches/"
LINES = "/api/payouts/lines/"


@pytest.fixture
def settlement(bkash, dhaka_mart, ctg_traders):
    set_bank_details(dhaka_mart, ctg_traders)
    make_txn(bkash, dhaka_mart, "1000", ref="A")
    make_txn(bkash, ctg_traders, "2000", ref="B")
    return approve_settlement(create_settlement(bkash, YESTERDAY))


def run_batch(api, **payload):
    response = api.post(f"{BATCHES}run/", payload, format="json")
    assert response.status_code == 201, response.data
    return response.data


def post(api, path, payload=None, expect=200):
    response = api.post(path, payload or {}, format="json")
    assert response.status_code == expect, response.data
    return response.data


def exported_batch(api):
    batch = run_batch(api)
    post(api, f"{BATCHES}{batch['id']}/approve/")
    return post(api, f"{BATCHES}{batch['id']}/export/")


def test_routes_require_authentication(settlement):
    assert APIClient().get(BATCHES).status_code == 401
    assert APIClient().post(f"{BATCHES}run/", {}, format="json").status_code == 401


def test_full_workflow_over_http(api, settlement, dhaka_mart):
    batch = run_batch(api, value_date="2026-11-08", minimum_amount="10.00")
    url = f"{BATCHES}{batch['id']}/"
    assert batch["status"] == "draft" and batch["total_amount"] == "3000.00"
    assert batch["minimum_amount"] == "10.00" and batch["created_by"] == api.user.pk
    assert batch["lines"][0]["settlements"] == [settlement.reference]

    assert api.get(f"{url}bank-file/").status_code == 409

    approved = post(api, f"{url}approve/")
    assert approved["status"] == "approved" and approved["approved_by_username"] == "ops"
    assert all(line["journal"] for line in approved["lines"])

    exported = post(api, f"{url}export/")
    assert exported["status"] == "exported"

    download = api.get(f"{url}bank-file/")
    assert download.status_code == 200 and download["Content-Type"] == "text/csv"
    assert f"{batch['reference']}.dcn1.csv" in download["Content-Disposition"]
    content = download.content.decode()
    assert bank_file.sha256(content) == exported["bank_file_sha256"]
    assert bank_file.parse(content).total == Decimal("3000.00")

    line = next(ln for ln in exported["lines"] if ln["merchant"] == dhaka_mart.pk)
    failed = post(api, f"{LINES}{line['id']}/fail/", {"reason": "closed account"})
    assert failed["status"] == "failed" and failed["reversal_journal"]
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("1000.00")

    closed = post(api, f"{url}mark-paid/", {"bank_reference": "BANK-9"})
    assert closed["status"] == "paid"
    assert closed["outcomes"] == {
        "failed": {"count": 1, "amount": "1000.00"},
        "paid": {"count": 1, "amount": "2000.00"},
    }
    assert api.get("/api/ledger/trial-balance/").data["balanced"] is True


def test_single_line_paid_then_returned(api, settlement, dhaka_mart):
    batch = exported_batch(api)
    line = next(ln for ln in batch["lines"] if ln["merchant"] == dhaka_mart.pk)
    paid = post(api, f"{LINES}{line['id']}/mark-paid/", {"bank_reference": "BANK-1"})
    assert paid["status"] == "paid" and paid["bank_reference"] == "BANK-1"
    returned = post(api, f"{LINES}{line['id']}/return/", {"reason": "name mismatch"})
    assert returned["status"] == "returned" and returned["failure_reason"] == "name mismatch"


def test_cancel_reverses_an_approved_batch(api, settlement, dhaka_mart):
    batch = run_batch(api)
    url = f"{BATCHES}{batch['id']}/"
    post(api, f"{url}approve/")
    cancelled = post(api, f"{url}cancel/", {"reason": "wrong value date"})
    assert cancelled["status"] == "cancelled"
    assert cancelled["outcomes"]["cancelled"]["count"] == 2
    assert ledger.merchant_payable(dhaka_mart).balance() == Decimal("1000.00")


@pytest.mark.parametrize("path", ["export/", "mark-paid/"])
def test_illegal_transitions_are_conflicts(api, settlement, path):
    batch = run_batch(api)
    response = api.post(f"{BATCHES}{batch['id']}/{path}", {}, format="json")
    assert response.status_code == 409
    assert "cannot move from draft" in response.data["detail"]


def test_double_approval_is_a_conflict(api, settlement):
    batch = run_batch(api)
    post(api, f"{BATCHES}{batch['id']}/approve/")
    post(api, f"{BATCHES}{batch['id']}/approve/", expect=409)


def test_stale_batch_is_a_conflict(api, settlement, dhaka_mart):
    batch = run_batch(api)
    today = timezone.localdate()
    issue_invoice(
        generate_invoice(dhaka_mart, today - timedelta(days=30), today + timedelta(days=1))
    )
    data = post(api, f"{BATCHES}{batch['id']}/approve/", expect=409)
    assert "is stale" in data["detail"]


def test_run_with_nothing_to_pay_is_a_bad_request(api, settlement):
    run_batch(api)
    data = post(api, f"{BATCHES}run/", expect=400)
    assert "no merchant is eligible" in data["detail"]
    assert PayoutBatch.objects.count() == 1


def test_run_validates_input(api, settlement):
    assert api.post(f"{BATCHES}run/", {"minimum_amount": "-1"}, format="json").status_code == 400
    assert api.post(f"{BATCHES}run/", {"merchants": [9999]}, format="json").status_code == 400


def test_run_can_target_merchants(api, settlement, dhaka_mart):
    batch = run_batch(api, merchants=[dhaka_mart.pk])
    assert [line["merchant_code"] for line in batch["lines"]] == ["DHAKAMART"]


def test_fail_requires_a_reason(api, settlement):
    batch = exported_batch(api)
    response = api.post(f"{LINES}{batch['lines'][0]['id']}/fail/", {}, format="json")
    assert response.status_code == 400 and "reason" in response.data


def test_list_filters_and_omits_the_lines(api, settlement, dhaka_mart):
    batch = run_batch(api)
    results = api.get(BATCHES, {"status": "draft"}).data["results"]
    assert [b["id"] for b in results] == [batch["id"]]
    assert "lines" not in results[0] and "bank_file" not in results[0]
    assert api.get(BATCHES, {"status": "paid"}).data["count"] == 0
    assert api.get(LINES, {"merchant": dhaka_mart.pk}).data["count"] == 1
    assert api.get(LINES, {"batch": batch["id"], "status": "pending"}).data["count"] == 2


def test_payout_routes_appear_in_the_schema(api):
    paths = api.get("/api/schema/?format=json").json()["paths"]
    assert "/api/payouts/batches/{id}/bank-file/" in paths
    assert "/api/payouts/lines/{id}/return/" in paths


def test_admin_is_read_only(settlement, api, client):
    batch = run_batch(api)
    admin = get_user_model().objects.create_superuser("root", "root@example.com", "pass12345")
    client.force_login(admin)
    assert client.get(reverse("admin:payouts_payoutbatch_changelist")).status_code == 200
    change_url = reverse("admin:payouts_payoutbatch_change", args=[batch["id"]])
    assert client.get(change_url).status_code == 200
    assert client.post(change_url, {"status": "paid"}).status_code == 403
    assert client.get(reverse("admin:payouts_payoutline_changelist")).status_code == 200
    assert client.get(reverse("admin:payouts_payoutallocation_changelist")).status_code == 200
