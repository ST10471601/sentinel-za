from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.customers import (
    BankingChannel,
    Customer,
    CustomerType,
    IncomeBand,
    IncomeSource,
    Province,
)

VALID_SA_ID_NUMBER = "9005155012081"  # born 1990-05-15, valid check digit


def individual_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "customer_id": "CUS-0000001",
        "customer_type": CustomerType.INDIVIDUAL,
        "full_name": "Thandiwe Dlamini",
        "sa_id_number": VALID_SA_ID_NUMBER,
        "company_reg_number": None,
        "date_of_birth": date(1990, 5, 15),
        "phone_number": "+27821234567",
        "email": "thandiwe.dlamini@inbox.example",
        "income_band": IncomeBand.MIDDLE,
        "income_source": IncomeSource.SALARY,
        "pay_day": 25,
        "home_province": Province.GAUTENG,
        "home_city": "Johannesburg",
        "home_lat": -26.2041,
        "home_lon": 28.0473,
        "preferred_channel": BankingChannel.APP,
        "onboarded_at": datetime(2020, 3, 1, 8, 0, tzinfo=UTC),
    }
    return fields | overrides


def business_fields(**overrides: object) -> dict[str, object]:
    fields = individual_fields(
        customer_type=CustomerType.BUSINESS,
        full_name="Baobab Logistics (Pty) Ltd",
        sa_id_number=None,
        company_reg_number="2015/123456/07",
        date_of_birth=None,
        income_band=IncomeBand.BUSINESS,
        income_source=IncomeSource.BUSINESS,
    )
    return fields | overrides


def test_valid_individual_and_business_are_accepted() -> None:
    assert Customer.model_validate(individual_fields()).customer_type is CustomerType.INDIVIDUAL
    assert Customer.model_validate(business_fields()).customer_type is CustomerType.BUSINESS


@pytest.mark.parametrize(
    "overrides",
    [
        {"sa_id_number": None},
        {"date_of_birth": None},
        {"company_reg_number": "2015/123456/07"},
    ],
)
def test_individual_needs_personal_identity_only(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="individuals need"):
        Customer.model_validate(individual_fields(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"company_reg_number": None},
        {"sa_id_number": VALID_SA_ID_NUMBER},
        {"date_of_birth": date(1990, 5, 15)},
    ],
)
def test_business_needs_registration_number_only(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="businesses need"):
        Customer.model_validate(business_fields(**overrides))


def test_sa_id_number_must_match_date_of_birth() -> None:
    with pytest.raises(ValidationError, match="does not match date_of_birth"):
        Customer.model_validate(individual_fields(date_of_birth=date(1991, 5, 15)))


def test_sa_id_number_must_have_valid_check_digit() -> None:
    with pytest.raises(ValidationError, match="invalid check digit"):
        Customer.model_validate(individual_fields(sa_id_number="9005155012082"))


def test_individual_cannot_use_business_income_band() -> None:
    with pytest.raises(ValidationError, match="only for business customers"):
        Customer.model_validate(individual_fields(income_band=IncomeBand.BUSINESS))


def test_business_must_use_business_income_band() -> None:
    with pytest.raises(ValidationError, match="only for business customers"):
        Customer.model_validate(business_fields(income_band=IncomeBand.HIGH))


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_id": "CUS-42"},
        {"phone_number": "0821234567"},
        {"email": "not-an-email"},
        {"pay_day": 0},
        {"home_lat": -91.0},
        {"pay_day": "25"},  # strict mode: no silent conversion
    ],
)
def test_malformed_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Customer.model_validate(individual_fields(**overrides))


def test_customers_are_immutable() -> None:
    customer = Customer.model_validate(individual_fields())
    with pytest.raises(ValidationError):
        customer.full_name = "Someone Else"  # type: ignore[misc]
