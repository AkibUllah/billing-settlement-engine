import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.accounts.models import Merchant

pytestmark = pytest.mark.django_db

VALID = {
    "bank_routing_number": "990000011",
    "bank_account_number": "7001000099",
    "bank_account_name": "Dhaka Mart Ltd.",
}


def test_complete_details_have_no_errors(dhaka_mart):
    for field, value in VALID.items():
        setattr(dhaka_mart, field, value)
    dhaka_mart.full_clean()
    assert dhaka_mart.bank_detail_errors() == []


def test_missing_details_are_named(dhaka_mart):
    assert dhaka_mart.bank_detail_errors() == [
        "bank_routing_number is missing",
        "bank_account_number is missing",
        "bank_account_name is missing",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bank_routing_number", "12345678"),
        ("bank_routing_number", "12345678a"),
        ("bank_routing_number", "১২৩৪৫৬৭৮৯"),
        ("bank_account_number", "12345"),
        ("bank_account_number", "12-34-56"),
        ("bank_account_number", "123456\n"),
        ("bank_account_name", "Evil\nD,1,2"),
        ("bank_account_name", "=HYPERLINK()"),
        ("bank_account_name", "Trailing newline\n"),
        ("bank_account_name", "ঢাকা মার্ট"),
    ],
)
def test_malformed_details_are_rejected_even_when_saved_directly(dhaka_mart, field, value):
    for name, good in VALID.items():
        setattr(dhaka_mart, name, good)
    setattr(dhaka_mart, field, value)
    with pytest.raises(ValidationError):
        dhaka_mart.full_clean()
    dhaka_mart.save()  # save() alone does not validate, which is why payouts re-check
    assert [e for e in dhaka_mart.bank_detail_errors() if e.startswith(field)]


def test_api_validates_bank_details(api, dhaka_mart):
    url = f"/api/merchants/{dhaka_mart.pk}/"
    bad = api.patch(url, {"bank_routing_number": "1234"}, format="json")
    assert bad.status_code == 400 and "bank_routing_number" in bad.data
    good = api.patch(url, VALID, format="json")
    assert good.status_code == 200 and good.data["bank_account_name"] == "Dhaka Mart Ltd."


def test_seeded_merchants_can_be_paid_out():
    call_command("seed_demo", days=1, per_day=1, verbosity=0)
    merchants = Merchant.objects.all()
    assert merchants.count() == 3
    assert all(m.bank_detail_errors() == [] for m in merchants)
