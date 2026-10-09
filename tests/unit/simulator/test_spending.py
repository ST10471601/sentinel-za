import random
import statistics
from datetime import UTC, date

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.customers import Customer, CustomerType, IncomeBand, IncomeSource
from sentinel.domain.merchants import MerchantCategory
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.spending import (
    ATM_STEP_CENTS,
    CATEGORY_AMOUNTS,
    CHILD_GRANT_CENTS,
    HOUR_WEIGHTS,
    MAIN_GRANT_CENTS,
    PAYDAY_BOOST,
    SPENDING_BY_BAND,
    WEEKDAY_WEIGHTS,
    TypicalAmount,
    activity_weight,
    atm_amount,
    card_amount,
    last_pay_date,
    pay_date,
    pick_event_time,
    pick_monthly_income,
    sample_amount,
    sample_daily_count,
)

DRAWS = 20_000


def rng() -> random.Random:
    return random.Random(42)


def test_every_band_has_a_spending_profile() -> None:
    assert set(SPENDING_BY_BAND) == set(IncomeBand)


def test_every_weighted_category_has_an_amount_and_merchants(
    reference_data: ReferenceData,
) -> None:
    merchant_categories = {merchant.category for merchant in reference_data.merchants}
    for profile in SPENDING_BY_BAND.values():
        assert set(profile.category_weights) <= set(CATEGORY_AMOUNTS)
        assert set(profile.category_weights) <= merchant_categories


def test_shares_are_between_zero_and_one() -> None:
    for profile in SPENDING_BY_BAND.values():
        assert 0 <= profile.contactless_share <= 1
        assert 0 <= profile.payshap_share <= 1


def test_weekday_weights_average_one_and_hours_cover_the_day() -> None:
    assert statistics.mean(WEEKDAY_WEIGHTS) == pytest.approx(1.0)
    assert len(HOUR_WEIGHTS) == 24


@pytest.mark.parametrize(
    ("band", "source"),
    [(IncomeBand.MIDDLE, IncomeSource.SALARY), (IncomeBand.LOW, IncomeSource.GRANT)],
)
def test_monthly_income_matches_band_and_source(
    reference_data: ReferenceData, band: IncomeBand, source: IncomeSource
) -> None:
    individual = next(
        customer
        for customer in reference_data.customers
        if customer.customer_type is CustomerType.INDIVIDUAL
    )
    customer = Customer.model_validate(
        individual.model_dump() | {"income_band": band, "income_source": source}
    )
    generator = rng()
    for _ in range(100):
        income = pick_monthly_income(generator, customer)
        if source is IncomeSource.GRANT:
            assert (income - MAIN_GRANT_CENTS) % CHILD_GRANT_CENTS == 0
        else:
            income_range = SPENDING_BY_BAND[band].monthly_income
            assert income_range.min_cents <= income <= income_range.max_cents
            assert income % 10_000 == 0


def test_sampled_amounts_centre_on_the_median() -> None:
    generator = rng()
    typical = TypicalAmount(median_cents=50_000, spread=0.8)
    amounts = [sample_amount(generator, typical) for _ in range(DRAWS)]
    assert statistics.median(amounts) == pytest.approx(50_000, rel=0.03)
    assert statistics.mean(amounts) > statistics.median(amounts)  # a long tail of big spends


def test_sampled_amounts_are_at_least_one_rand() -> None:
    generator = rng()
    tiny = TypicalAmount(median_cents=50, spread=0.1)
    assert all(sample_amount(generator, tiny) == 100 for _ in range(100))


def test_higher_bands_spend_more_on_the_same_category() -> None:
    def median_grocery_spend(band: IncomeBand) -> float:
        generator = rng()
        return statistics.median(
            card_amount(generator, MerchantCategory.GROCERY, band) for _ in range(DRAWS)
        )

    assert median_grocery_spend(IncomeBand.LOW) < median_grocery_spend(IncomeBand.HIGH)


def test_toll_fees_do_not_depend_on_income() -> None:
    low = [card_amount(rng(), MerchantCategory.TOLL, IncomeBand.LOW) for _ in range(5)]
    high = [card_amount(rng(), MerchantCategory.TOLL, IncomeBand.HIGH) for _ in range(5)]
    assert low == high


def test_atm_withdrawals_come_in_whole_steps() -> None:
    generator = rng()
    amounts = [atm_amount(generator, IncomeBand.LOW) for _ in range(1_000)]
    assert all(amount % ATM_STEP_CENTS == 0 and amount >= ATM_STEP_CENTS for amount in amounts)


@pytest.mark.parametrize(
    ("pay_day", "year", "month", "expected"),
    [
        (25, 2026, 2, date(2026, 2, 25)),  # Wednesday: unchanged
        (25, 2026, 1, date(2026, 1, 23)),  # Sunday: back to Friday
        (31, 2026, 2, date(2026, 2, 27)),  # past month end, then Saturday: back to Friday
        (1, 2026, 2, date(2026, 1, 30)),  # Sunday the 1st: back into the previous month
    ],
)
def test_pay_date_moves_weekends_back_to_friday(
    pay_day: int, year: int, month: int, expected: date
) -> None:
    assert pay_date(year, month, pay_day) == expected


@pytest.mark.parametrize(
    ("day", "pay_day", "expected"),
    [
        (date(2026, 3, 10), 25, date(2026, 2, 25)),
        (date(2026, 3, 25), 25, date(2026, 3, 25)),
        (date(2026, 1, 31), 1, date(2026, 1, 30)),  # February's pay landed in January
        (date(2026, 1, 1), 25, date(2025, 12, 25)),
    ],
)
def test_last_pay_date_is_the_latest_on_or_before_the_day(
    day: date, pay_day: int, expected: date
) -> None:
    assert last_pay_date(day, pay_day) == expected


def test_payday_boost_fades_over_the_following_days() -> None:
    paid = date(2026, 2, 25)  # a Wednesday
    assert activity_weight(paid, paid) == pytest.approx(WEEKDAY_WEIGHTS[2] * PAYDAY_BOOST[0])
    week_later = date(2026, 3, 4)  # also a Wednesday, boost over
    assert activity_weight(week_later, paid) == pytest.approx(WEEKDAY_WEIGHTS[2])


def test_daily_counts_average_the_expected_rate() -> None:
    generator = rng()
    counts = [sample_daily_count(generator, per_month=30.44, weight=2.5) for _ in range(DRAWS)]
    assert statistics.mean(counts) == pytest.approx(2.5, rel=0.03)


def test_zero_rate_gives_no_events_and_negative_rate_is_rejected() -> None:
    assert sample_daily_count(rng(), per_month=0, weight=1.0) == 0
    with pytest.raises(ValueError, match="negative"):
        sample_daily_count(rng(), per_month=-1, weight=1.0)


def test_event_times_fall_on_the_sa_calendar_day_and_skew_to_daytime() -> None:
    generator = rng()
    day = date(2026, 3, 6)
    times = [pick_event_time(generator, day) for _ in range(2_000)]
    assert all(moment.tzinfo is UTC and to_sast(moment).date() == day for moment in times)

    hours = [to_sast(moment).hour for moment in times]
    assert sum(1 for hour in hours if 8 <= hour < 20) > 0.8 * len(hours)


def test_event_time_is_reproducible_from_the_seed() -> None:
    day = date(2026, 3, 6)
    assert pick_event_time(rng(), day) == pick_event_time(rng(), day)
