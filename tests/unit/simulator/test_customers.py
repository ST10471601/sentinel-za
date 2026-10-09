import random
from collections import Counter
from datetime import UTC, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import add_years
from sentinel.core.errors import SimulationError
from sentinel.domain.customers import (
    BankingChannel,
    Customer,
    CustomerType,
    IncomeBand,
    IncomeSource,
)
from sentinel.simulator import customers as customers_module
from sentinel.simulator.customers import (
    LAST_GRANT_PAY_DAY,
    MIN_AGE,
    RECENT_JOINER_DAYS,
    generate_customers,
)
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData

START = DEFAULT_SIMULATION_START


@pytest.fixture(scope="module")
def customers(reference_data: ReferenceData) -> tuple[Customer, ...]:
    return reference_data.customers


def test_ids_are_sequential(customers: tuple[Customer, ...]) -> None:
    assert [c.customer_id for c in customers[:3]] == ["CUS-0000001", "CUS-0000002", "CUS-0000003"]
    assert customers[-1].customer_id == f"CUS-{len(customers):07d}"


def test_about_five_percent_are_businesses(customers: tuple[Customer, ...]) -> None:
    businesses = [c for c in customers if c.customer_type is CustomerType.BUSINESS]
    assert 0.03 <= len(businesses) / len(customers) <= 0.07


@pytest.mark.parametrize("field", ["phone_number", "email", "sa_id_number", "company_reg_number"])
def test_identifiers_are_unique(customers: tuple[Customer, ...], field: str) -> None:
    values = [getattr(c, field) for c in customers if getattr(c, field) is not None]
    assert len(values) == len(set(values))


def test_business_names_are_unique(customers: tuple[Customer, ...]) -> None:
    names = [c.full_name for c in customers if c.customer_type is CustomerType.BUSINESS]
    assert len(names) == len(set(names))


def test_everyone_joined_before_the_start(customers: tuple[Customer, ...]) -> None:
    assert all(c.onboarded_at < START for c in customers)


def test_individuals_joined_at_18_or_older(customers: tuple[Customer, ...]) -> None:
    for customer in customers:
        if customer.date_of_birth is None:
            continue
        eighteenth = datetime.combine(add_years(customer.date_of_birth, MIN_AGE), time(), UTC)
        assert customer.onboarded_at >= eighteenth, customer.customer_id


def test_some_customers_joined_recently(customers: tuple[Customer, ...]) -> None:
    recent_cutoff = START - timedelta(days=RECENT_JOINER_DAYS)
    recent = [c for c in customers if c.onboarded_at >= recent_cutoff]
    assert 0.05 <= len(recent) / len(customers) <= 0.20


def test_grants_are_paid_early_in_the_month(customers: tuple[Customer, ...]) -> None:
    grant_pay_days = [c.pay_day for c in customers if c.income_source is IncomeSource.GRANT]
    assert grant_pay_days
    assert max(grant_pay_days) <= LAST_GRANT_PAY_DAY


def test_ussd_is_used_mainly_by_low_income_customers(customers: tuple[Customer, ...]) -> None:
    ussd_by_band = Counter(
        c.income_band for c in customers if c.preferred_channel is BankingChannel.USSD
    )
    assert ussd_by_band[IncomeBand.LOW] > 5 * ussd_by_band[IncomeBand.MIDDLE]
    assert ussd_by_band[IncomeBand.HIGH] == 0


def test_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        generate_customers(random.Random(1), 0, START)


def test_running_out_of_business_names_is_a_simulation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(customers_module, "BUSINESS_SHARE", 1.0)
    monkeypatch.setattr(customers_module, "BUSINESS_TRADES", ("Trading",))
    with pytest.raises(SimulationError, match="business names available"):
        generate_customers(random.Random(1), 100, START)
