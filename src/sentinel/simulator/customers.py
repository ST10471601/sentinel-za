"""Generate bank customers: individuals and businesses."""

import random
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import add_years
from sentinel.core.errors import SimulationError
from sentinel.domain.customers import (
    BankingChannel,
    Customer,
    CustomerType,
    IncomeBand,
    IncomeSource,
)
from sentinel.simulator.geography import CITIES, City, jitter_location
from sentinel.simulator.identifiers import (
    make_company_reg_number,
    make_id,
    make_phone_number,
    make_sa_id_number,
)
from sentinel.simulator.names import (
    BUSINESS_NAME_WORDS,
    BUSINESS_TRADES,
    FEMALE_FIRST_NAMES,
    MALE_FIRST_NAMES,
    SURNAMES,
)
from sentinel.simulator.randomness import chance, draw_unique, pick, random_datetime_between

BUSINESS_SHARE = 0.05

# Ages at the simulation start, drawn from a triangle that peaks at young adults.
MIN_AGE, MODE_AGE, MAX_AGE = 18, 32, 85

MAX_TENURE_YEARS = 15
RECENT_JOINER_SHARE = 0.10  # joined in the last six months, so "new" is not a fraud tell
RECENT_JOINER_DAYS = 182
MAX_BUSINESS_AGE_AT_JOINING = 10  # years a business existed before banking with us

HOME_JITTER_DEGREES = 0.05  # about 5 km around the city centre

# The reserved .example domain can never be a real mailbox.
EMAIL_DOMAINS = ("inbox.example", "mail.example", "post.example")

INCOME_BAND_WEIGHTS: dict[IncomeBand, float] = {
    IncomeBand.LOW: 45,
    IncomeBand.MIDDLE: 40,
    IncomeBand.HIGH: 15,
}

INCOME_SOURCE_WEIGHTS: dict[IncomeBand, dict[IncomeSource, float]] = {
    IncomeBand.LOW: {IncomeSource.SALARY: 50, IncomeSource.GRANT: 40, IncomeSource.MIXED: 10},
    IncomeBand.MIDDLE: {IncomeSource.SALARY: 85, IncomeSource.MIXED: 15},
    IncomeBand.HIGH: {IncomeSource.SALARY: 75, IncomeSource.MIXED: 25},
}

# Low-income customers lean towards USSD; businesses mostly use internet banking.
CHANNEL_WEIGHTS: dict[IncomeBand, dict[BankingChannel, float]] = {
    IncomeBand.LOW: {BankingChannel.APP: 50, BankingChannel.USSD: 45, BankingChannel.INTERNET: 5},
    IncomeBand.MIDDLE: {
        BankingChannel.APP: 85,
        BankingChannel.INTERNET: 10,
        BankingChannel.USSD: 5,
    },
    IncomeBand.HIGH: {BankingChannel.APP: 80, BankingChannel.INTERNET: 20},
    IncomeBand.BUSINESS: {BankingChannel.INTERNET: 60, BankingChannel.APP: 40},
}

# Most salaries are paid on the 25th.
SALARY_PAY_DAY_WEIGHTS: dict[int, float] = {25: 80, 15: 10, 20: 5, 1: 5}
LAST_GRANT_PAY_DAY = 5  # SASSA grants are paid in the first days of the month


@dataclass(slots=True)
class _UsedIdentifiers:
    """Values that must be unique across all customers."""

    phone_numbers: set[str] = field(default_factory=set)
    sa_id_numbers: set[str] = field(default_factory=set)
    company_reg_numbers: set[str] = field(default_factory=set)


def generate_customers(
    rng: random.Random, count: int, simulation_start: datetime
) -> list[Customer]:
    """Generate ``count`` customers who all joined before ``simulation_start``."""
    if count < 1:
        raise ValueError(f"customer count must be at least 1, got {count}")

    is_business_flags = [chance(rng, BUSINESS_SHARE) for _ in range(count)]
    business_names = _pick_business_names(rng, sum(is_business_flags))
    used = _UsedIdentifiers()

    customers = []
    for number, is_business in enumerate(is_business_flags, start=1):
        customer_id = make_id("CUS", number)
        if is_business:
            name = business_names.pop()
            customer = _make_business(rng, customer_id, name, simulation_start, used)
        else:
            customer = _make_individual(rng, customer_id, number, simulation_start, used)
        customers.append(customer)
    return customers


def _make_individual(
    rng: random.Random,
    customer_id: str,
    number: int,
    simulation_start: datetime,
    used: _UsedIdentifiers,
) -> Customer:
    is_female = chance(rng, 0.5)
    first_name = rng.choice(FEMALE_FIRST_NAMES if is_female else MALE_FIRST_NAMES)
    surname = rng.choice(SURNAMES)
    date_of_birth = _pick_date_of_birth(rng, simulation_start.date())
    income_band = pick(rng, INCOME_BAND_WEIGHTS)
    income_source = pick(rng, INCOME_SOURCE_WEIGHTS[income_band])
    city, home_lat, home_lon = _pick_home(rng)

    # Customers join at 18 or later.
    earliest_join = max(
        _midnight_utc(add_years(date_of_birth, MIN_AGE)),
        simulation_start - timedelta(days=365 * MAX_TENURE_YEARS),
    )

    return Customer(
        customer_id=customer_id,
        customer_type=CustomerType.INDIVIDUAL,
        full_name=f"{first_name} {surname}",
        sa_id_number=draw_unique(
            lambda: make_sa_id_number(rng, date_of_birth, is_female=is_female),
            used.sa_id_numbers,
        ),
        company_reg_number=None,
        date_of_birth=date_of_birth,
        phone_number=draw_unique(lambda: make_phone_number(rng), used.phone_numbers),
        email=f"{_slug(first_name)}.{_slug(surname)}{number}@{rng.choice(EMAIL_DOMAINS)}",
        income_band=income_band,
        income_source=income_source,
        pay_day=_pick_pay_day(rng, income_source),
        home_province=city.province,
        home_city=city.name,
        home_lat=home_lat,
        home_lon=home_lon,
        preferred_channel=pick(rng, CHANNEL_WEIGHTS[income_band]),
        onboarded_at=_pick_onboarded_at(rng, earliest_join, simulation_start),
    )


def _make_business(
    rng: random.Random,
    customer_id: str,
    name: str,
    simulation_start: datetime,
    used: _UsedIdentifiers,
) -> Customer:
    city, home_lat, home_lon = _pick_home(rng)
    earliest_join = simulation_start - timedelta(days=365 * MAX_TENURE_YEARS)
    onboarded_at = _pick_onboarded_at(rng, earliest_join, simulation_start)
    registration_year = onboarded_at.year - rng.randint(0, MAX_BUSINESS_AGE_AT_JOINING)

    return Customer(
        customer_id=customer_id,
        customer_type=CustomerType.BUSINESS,
        full_name=f"{name} (Pty) Ltd",
        sa_id_number=None,
        company_reg_number=draw_unique(
            lambda: make_company_reg_number(rng, registration_year),
            used.company_reg_numbers,
        ),
        date_of_birth=None,
        phone_number=draw_unique(lambda: make_phone_number(rng), used.phone_numbers),
        email=f"accounts.{_slug(name)}@{rng.choice(EMAIL_DOMAINS)}",
        income_band=IncomeBand.BUSINESS,
        income_source=IncomeSource.BUSINESS,
        pay_day=_pick_pay_day(rng, IncomeSource.BUSINESS),
        home_province=city.province,
        home_city=city.name,
        home_lat=home_lat,
        home_lon=home_lon,
        preferred_channel=pick(rng, CHANNEL_WEIGHTS[IncomeBand.BUSINESS]),
        onboarded_at=onboarded_at,
    )


def _pick_business_names(rng: random.Random, count: int) -> list[str]:
    """Pick ``count`` distinct names such as "Baobab Logistics"."""
    all_names = [f"{word} {trade}" for word in BUSINESS_NAME_WORDS for trade in BUSINESS_TRADES]
    if count > len(all_names):
        raise SimulationError(f"only {len(all_names)} business names available, need {count}")
    return rng.sample(all_names, count)


def _pick_date_of_birth(rng: random.Random, start_day: date) -> date:
    """Pick a birth date so the customer is at least ``MIN_AGE`` on ``start_day``."""
    age = int(rng.triangular(MIN_AGE, MAX_AGE, MODE_AGE))
    # Born 1 to 365 days before turning `age`, so the 18th birthday is always in the past.
    return add_years(start_day, -age) - timedelta(days=rng.randint(1, 365))


def _pick_home(rng: random.Random) -> tuple[City, float, float]:
    """Pick a city by weight and a home location near its centre."""
    city = rng.choices(CITIES, weights=[city.weight for city in CITIES])[0]
    home_lat, home_lon = jitter_location(rng, city, HOME_JITTER_DEGREES)
    return city, home_lat, home_lon


def _pick_pay_day(rng: random.Random, income_source: IncomeSource) -> int:
    if income_source is IncomeSource.GRANT:
        return rng.randint(1, LAST_GRANT_PAY_DAY)
    if income_source is IncomeSource.BUSINESS:
        # Business income arrives all month; this is its busiest day.
        return rng.randint(1, 28)
    return pick(rng, SALARY_PAY_DAY_WEIGHTS)


def _pick_onboarded_at(
    rng: random.Random, earliest: datetime, simulation_start: datetime
) -> datetime:
    if chance(rng, RECENT_JOINER_SHARE):
        earliest = max(earliest, simulation_start - timedelta(days=RECENT_JOINER_DAYS))
    return random_datetime_between(rng, earliest, simulation_start)


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


def _slug(text: str) -> str:
    """Lowercase letters and digits only: "Van der Merwe" -> "vandermerwe"."""
    return re.sub(r"[^a-z0-9]", "", text.lower())
