"""How much each income band earns and spends, and when activity happens."""

import math
import random
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST
from sentinel.domain.customers import Customer, IncomeBand, IncomeSource
from sentinel.domain.merchants import MerchantCategory


@dataclass(frozen=True, slots=True)
class TypicalAmount:
    """A log-normal amount: most draws sit near the median, a few far above it."""

    median_cents: int
    spread: float  # log-normal sigma; 0.5 is tight, 1.0 is wide


@dataclass(frozen=True, slots=True)
class IncomeRange:
    """Lowest and highest monthly income in a band, in cents."""

    min_cents: int
    max_cents: int


@dataclass(frozen=True, slots=True)
class SpendingProfile:
    """How one income band earns and spends in a typical month."""

    monthly_income: IncomeRange
    card_purchases_per_month: float
    category_weights: Mapping[MerchantCategory, float]
    amount_scale: float  # multiplies the middle-band amounts in CATEGORY_AMOUNTS
    contactless_share: float  # of in-person card purchases; the rest use chip and PIN
    atm_withdrawals_per_month: float
    atm_amount: TypicalAmount
    payments_per_month: float  # EFT and PayShap to saved beneficiaries
    payment_amount: TypicalAmount
    payshap_share: float  # of those payments
    debit_order_range: tuple[int, int]  # how many monthly debit orders a customer has
    debit_order_amount: TypicalAmount


# Anchors: national minimum wage of R30.23/hour from March 2026 (about R5,900 for a 45-hour
# week), and Stats SA average formal-sector earnings of R29,997 (QES, February 2026).
SPENDING_BY_BAND: dict[IncomeBand, SpendingProfile] = {
    IncomeBand.LOW: SpendingProfile(
        monthly_income=IncomeRange(590_000, 1_200_000),
        card_purchases_per_month=18,
        category_weights={
            MerchantCategory.GROCERY: 40,
            MerchantCategory.AIRTIME: 20,
            MerchantCategory.RESTAURANT: 8,
            MerchantCategory.BETTING: 8,
            MerchantCategory.CLOTHING: 7,
            MerchantCategory.FUEL: 5,
            MerchantCategory.DIGITAL_GOODS: 4,
            MerchantCategory.ONLINE_RETAIL: 3,
        },
        amount_scale=0.6,
        contactless_share=0.6,
        atm_withdrawals_per_month=4,
        atm_amount=TypicalAmount(40_000, 0.6),
        payments_per_month=3,
        payment_amount=TypicalAmount(50_000, 0.8),
        payshap_share=0.4,
        debit_order_range=(1, 2),
        debit_order_amount=TypicalAmount(35_000, 0.6),
    ),
    IncomeBand.MIDDLE: SpendingProfile(
        monthly_income=IncomeRange(1_500_000, 4_500_000),
        card_purchases_per_month=35,
        category_weights={
            MerchantCategory.GROCERY: 30,
            MerchantCategory.FUEL: 15,
            MerchantCategory.RESTAURANT: 14,
            MerchantCategory.ONLINE_RETAIL: 10,
            MerchantCategory.AIRTIME: 8,
            MerchantCategory.CLOTHING: 8,
            MerchantCategory.DIGITAL_GOODS: 6,
            MerchantCategory.TOLL: 4,
            MerchantCategory.BETTING: 3,
            MerchantCategory.SOFTWARE: 2,
        },
        amount_scale=1.0,
        contactless_share=0.75,
        atm_withdrawals_per_month=2,
        atm_amount=TypicalAmount(80_000, 0.6),
        payments_per_month=5,
        payment_amount=TypicalAmount(150_000, 0.9),
        payshap_share=0.3,
        debit_order_range=(3, 5),
        debit_order_amount=TypicalAmount(90_000, 0.7),
    ),
    IncomeBand.HIGH: SpendingProfile(
        monthly_income=IncomeRange(5_000_000, 15_000_000),
        card_purchases_per_month=55,
        category_weights={
            MerchantCategory.GROCERY: 25,
            MerchantCategory.RESTAURANT: 18,
            MerchantCategory.FUEL: 14,
            MerchantCategory.ONLINE_RETAIL: 14,
            MerchantCategory.CLOTHING: 8,
            MerchantCategory.DIGITAL_GOODS: 6,
            MerchantCategory.TOLL: 6,
            MerchantCategory.SOFTWARE: 4,
            MerchantCategory.AIRTIME: 3,
            MerchantCategory.TRAVEL_AGENCY: 1,
            MerchantCategory.CRYPTO: 1,
        },
        amount_scale=1.8,
        contactless_share=0.85,
        atm_withdrawals_per_month=1,
        atm_amount=TypicalAmount(150_000, 0.6),
        payments_per_month=8,
        payment_amount=TypicalAmount(400_000, 1.0),
        payshap_share=0.25,
        debit_order_range=(5, 8),
        debit_order_amount=TypicalAmount(250_000, 0.8),
    ),
    IncomeBand.BUSINESS: SpendingProfile(
        monthly_income=IncomeRange(8_000_000, 60_000_000),  # turnover, not salary
        card_purchases_per_month=25,
        category_weights={
            MerchantCategory.FUEL: 30,
            MerchantCategory.GROCERY: 15,
            MerchantCategory.TOLL: 10,
            MerchantCategory.SOFTWARE: 10,
            MerchantCategory.ADVERTISING: 10,
            MerchantCategory.ONLINE_RETAIL: 10,
            MerchantCategory.RESTAURANT: 10,
            MerchantCategory.AIRTIME: 5,
        },
        amount_scale=2.0,
        contactless_share=0.5,
        atm_withdrawals_per_month=2,
        atm_amount=TypicalAmount(300_000, 0.6),
        payments_per_month=30,  # suppliers and wages
        payment_amount=TypicalAmount(1_200_000, 1.0),
        payshap_share=0.15,
        debit_order_range=(3, 6),
        debit_order_amount=TypicalAmount(500_000, 0.8),
    ),
}

# Middle-band card amounts. Absa saw average baskets of R496 in store and R827 online on
# Black Friday 2025, about 25% up on the year before, so normal days sit below that.
CATEGORY_AMOUNTS: dict[MerchantCategory, TypicalAmount] = {
    MerchantCategory.GROCERY: TypicalAmount(35_000, 0.8),
    MerchantCategory.FUEL: TypicalAmount(60_000, 0.5),
    MerchantCategory.TOLL: TypicalAmount(6_000, 0.4),
    MerchantCategory.RESTAURANT: TypicalAmount(18_000, 0.7),
    MerchantCategory.CLOTHING: TypicalAmount(55_000, 0.7),
    MerchantCategory.ONLINE_RETAIL: TypicalAmount(65_000, 0.9),
    MerchantCategory.DIGITAL_GOODS: TypicalAmount(12_000, 0.8),
    MerchantCategory.SOFTWARE: TypicalAmount(30_000, 0.8),
    MerchantCategory.ADVERTISING: TypicalAmount(150_000, 1.0),
    MerchantCategory.TRAVEL_AGENCY: TypicalAmount(450_000, 0.8),
    MerchantCategory.BETTING: TypicalAmount(10_000, 1.0),
    MerchantCategory.CRYPTO: TypicalAmount(100_000, 1.0),
    MerchantCategory.AIRTIME: TypicalAmount(5_000, 0.7),
}
FIXED_PRICE_CATEGORIES = frozenset({MerchantCategory.TOLL})  # same fee whatever you earn

# SASSA amounts from April 2026: old-age or disability grant R2,400, child support R580.
MAIN_GRANT_CENTS = 240_000
CHILD_GRANT_CENTS = 58_000
MAX_CHILD_GRANTS = 2
INCOME_STEP_CENTS = 10_000  # simulation assumption: salaries are whole R100 amounts

ATM_STEP_CENTS = 5_000  # simulation assumption: withdrawals come in R50 steps
MIN_AMOUNT_CENTS = 100  # R1

# Share of activity by hour of day in SA time: quiet at night, peaks at lunch and after work.
HOUR_WEIGHTS: tuple[float, ...] = (
    0.2, 0.1, 0.05, 0.05, 0.1, 0.4, 1.0, 2.0, 2.5, 3.0, 3.5, 4.0,
    5.0, 4.5, 4.0, 4.5, 5.0, 6.0, 5.5, 4.0, 3.0, 2.0, 1.0, 0.5,
)  # fmt: skip

# Monday to Sunday. They average 1.0 so a month's total volume stays as configured.
WEEKDAY_WEIGHTS: tuple[float, ...] = (0.9, 0.85, 0.9, 0.95, 1.25, 1.35, 0.8)

# Extra spending on payday and the days right after it, then back to normal.
PAYDAY_BOOST: tuple[float, ...] = (1.8, 1.6, 1.4, 1.2, 1.1)

DAYS_PER_MONTH = 30.44
FRIDAY = 4


def pick_monthly_income(rng: random.Random, customer: Customer) -> int:
    """Pick the customer's regular monthly income, in cents."""
    if customer.income_source is IncomeSource.GRANT:
        child_grants = rng.randint(0, MAX_CHILD_GRANTS)
        return MAIN_GRANT_CENTS + child_grants * CHILD_GRANT_CENTS

    income = SPENDING_BY_BAND[customer.income_band].monthly_income
    cents = rng.randint(income.min_cents, income.max_cents)
    return round(cents / INCOME_STEP_CENTS) * INCOME_STEP_CENTS


def sample_amount(rng: random.Random, typical: TypicalAmount, scale: float = 1.0) -> int:
    """Draw an amount in cents around the typical median, multiplied by ``scale``."""
    cents = rng.lognormvariate(math.log(typical.median_cents * scale), typical.spread)
    return max(MIN_AMOUNT_CENTS, round(cents))


def card_amount(rng: random.Random, category: MerchantCategory, band: IncomeBand) -> int:
    """Draw a card purchase amount for the category, sized to the income band."""
    scale = 1.0 if category in FIXED_PRICE_CATEGORIES else SPENDING_BY_BAND[band].amount_scale
    return sample_amount(rng, CATEGORY_AMOUNTS[category], scale)


def atm_amount(rng: random.Random, band: IncomeBand) -> int:
    """Draw an ATM withdrawal, rounded to whole R50 steps."""
    cents = sample_amount(rng, SPENDING_BY_BAND[band].atm_amount)
    return max(ATM_STEP_CENTS, round(cents / ATM_STEP_CENTS) * ATM_STEP_CENTS)


def pay_date(year: int, month: int, pay_day: int) -> date:
    """Return the date income actually lands in a month.

    A pay day past the month's end falls on its last day, and a weekend pay day moves back
    to the Friday before, as SA employers and SASSA do. Public holidays are ignored.
    """
    last_day = monthrange(year, month)[1]
    scheduled = date(year, month, min(pay_day, last_day))
    days_after_friday = max(scheduled.weekday() - FRIDAY, 0)
    return scheduled - timedelta(days=days_after_friday)


def last_pay_date(day: date, pay_day: int) -> date:
    """Return the most recent pay date on or before ``day``."""
    # Check next month too: a pay day on the 1st can move back into this month.
    candidates = [pay_date(*_shift_month(day, offset), pay_day) for offset in (-1, 0, 1)]
    return max(candidate for candidate in candidates if candidate <= day)


def activity_weight(day: date, last_paid: date) -> float:
    """How busy a day is compared with an average day (1.0)."""
    weight = WEEKDAY_WEIGHTS[day.weekday()]
    days_since_pay = (day - last_paid).days
    if 0 <= days_since_pay < len(PAYDAY_BOOST):
        weight *= PAYDAY_BOOST[days_since_pay]
    return weight


def sample_daily_count(rng: random.Random, per_month: float, weight: float) -> int:
    """Draw how many times something happens on one day.

    Uses a Poisson draw: events happen independently, at a rate of ``per_month`` scaled
    to one day and multiplied by the day's ``weight``.
    """
    expected = per_month / DAYS_PER_MONTH * weight
    if expected < 0:
        raise ValueError(f"expected count must not be negative: {expected}")

    # Knuth's method: multiply uniform draws until the product drops below e^-expected.
    # Fine for the small daily counts used here.
    threshold = math.exp(-expected)
    count, product = 0, rng.random()
    while product > threshold:
        count += 1
        product *= rng.random()
    return count


def pick_event_time(rng: random.Random, day: date) -> datetime:
    """Pick a time on ``day`` (SA calendar day) at a typical hour, returned in UTC."""
    hour = rng.choices(range(len(HOUR_WEIGHTS)), weights=HOUR_WEIGHTS)[0]
    local_time = datetime.combine(day, time(hour), tzinfo=SAST)
    return (local_time + timedelta(seconds=rng.randrange(3600))).astimezone(UTC)


def _shift_month(day: date, offset: int) -> tuple[int, int]:
    """Return the (year, month) that is ``offset`` months from ``day``'s month."""
    year, month_index = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return year, month_index + 1
