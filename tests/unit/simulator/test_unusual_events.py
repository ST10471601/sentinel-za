import random
from collections import Counter
from datetime import date, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Account, Card
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEventType
from sentinel.domain.customers import Customer, CustomerType
from sentinel.domain.merchants import MerchantCategory
from sentinel.domain.transactions import Channel, TransactionStatus
from sentinel.simulator.beneficiaries import PayeeHistory
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import MerchantIndex, plan_card_habits
from sentinel.simulator.drafts import Settlement, to_transaction
from sentinel.simulator.geography import CITIES
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import PaymentHabits, plan_payment_habits
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.sessions import LinkedDevice
from sentinel.simulator.spending import CATEGORY_AMOUNTS
from sentinel.simulator.unusual_events import (
    BOOKING_DAYS_AHEAD,
    TRIP_DAYS,
    BigPurchase,
    EventIds,
    NewPayee,
    NewPhone,
    Trip,
    UnusualEvent,
    add_and_pay_new_payee,
    away_customer,
    big_purchase_draft,
    plan_unusual_events,
    register_new_phone,
    trip_on,
)

FIRST_DAY = date(2026, 1, 1)
SIX_MONTHS = 181
RICH = 10**12
APPROVED = Settlement(TransactionStatus.APPROVED, None, 0)

type Plans = list[tuple[Customer, tuple[UnusualEvent, ...]]]


def rng() -> random.Random:
    return random.Random(12)


def event_ids() -> EventIds:
    return EventIds(
        sessions=IdSequence("SES", start_after=100),
        devices=IdSequence("DEV", start_after=100),
        beneficiaries=IdSequence("BEN", start_after=100),
        beneficiary_events=IdSequence("BEV", start_after=100),
    )


@pytest.fixture(scope="module")
def individual(reference_data: ReferenceData) -> Customer:
    return next(c for c in reference_data.customers if c.customer_type is CustomerType.INDIVIDUAL)


@pytest.fixture(scope="module")
def plans(reference_data: ReferenceData) -> Plans:
    generator = rng()
    return [
        (customer, plan_unusual_events(generator, customer, FIRST_DAY, SIX_MONTHS))
        for customer in reference_data.customers
    ]


@pytest.fixture(scope="module")
def payment_habits(
    individual: Customer,
    accounts_by_customer: dict[str, list[Account]],
    devices_by_customer: dict[str, list[LinkedDevice]],
    payees: PayeeHistory,
) -> PaymentHabits:
    own_payees: list[Beneficiary] = [
        b for b in payees.beneficiaries if b.customer_id == individual.customer_id
    ]
    return plan_payment_habits(
        accounts_by_customer[individual.customer_id],
        own_payees,
        devices_by_customer[individual.customer_id],
        monthly_income_cents=2_000_000,
    )


def test_event_counts_follow_the_monthly_rates(
    plans: Plans,
) -> None:
    counts = Counter(type(event).__name__ for _, events in plans for event in events)
    customers = len(plans)
    months = SIX_MONTHS / 30.44
    # Rates per customer per month: trip 0.12 (individuals), phone 0.02, payee 0.12.
    assert counts["NewPhone"] / customers / months == pytest.approx(0.02, abs=0.01)
    assert counts["NewPayee"] / customers / months == pytest.approx(0.12, abs=0.02)
    assert counts["Trip"] / customers / months == pytest.approx(0.12 * 0.95, abs=0.02)


def test_events_fall_inside_the_simulation(
    plans: Plans,
) -> None:
    last_day = FIRST_DAY + timedelta(days=SIX_MONTHS - 1)
    for _, events in plans:
        for event in events:
            day = event.start if isinstance(event, Trip) else event.day
            assert FIRST_DAY <= day <= last_day


def test_businesses_do_not_take_trips(
    plans: Plans,
) -> None:
    for customer, events in plans:
        if customer.customer_type is CustomerType.BUSINESS:
            assert not any(isinstance(event, Trip) for event in events)


def test_trips_go_to_another_city_and_are_booked_beforehand(
    plans: Plans,
) -> None:
    trips = 0
    for customer, events in plans:
        bookings = {
            e.day
            for e in events
            if isinstance(e, BigPurchase) and e.category is MerchantCategory.TRAVEL_AGENCY
        }
        for trip in (e for e in events if isinstance(e, Trip)):
            trips += 1
            assert trip.city.name != customer.home_city
            assert TRIP_DAYS[0] <= (trip.end - trip.start).days + 1 <= TRIP_DAYS[1]
            earliest_booking = trip.start - timedelta(days=BOOKING_DAYS_AHEAD[1])
            if earliest_booking >= FIRST_DAY:
                assert any(earliest_booking <= day < trip.start for day in bookings)
    assert trips


def test_trip_on_finds_the_trip_covering_a_day() -> None:
    trip = Trip(date(2026, 2, 10), date(2026, 2, 12), CITIES[0])
    events = (NewPhone(date(2026, 2, 11)), trip)
    assert trip_on(events, date(2026, 2, 12)) is trip
    assert trip_on(events, date(2026, 2, 13)) is None


def test_away_customer_shops_and_logs_in_near_the_destination(
    individual: Customer,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
) -> None:
    city = next(c for c in CITIES if c.name != individual.home_city)
    away = away_customer(individual, city)
    assert (away.home_city, away.home_lat, away.customer_id) == (
        city.name,
        city.lat,
        individual.customer_id,
    )

    habits = plan_card_habits(rng(), away, cards_by_customer[individual.customer_id], index)
    shops = habits.options[MerchantCategory.GROCERY]
    assert shops and all(shop.city == city.name for shop in shops)


@pytest.mark.parametrize(
    "category",
    [MerchantCategory.ONLINE_RETAIL, MerchantCategory.CLOTHING, MerchantCategory.TRAVEL_AGENCY],
)
def test_big_purchase_is_far_above_a_normal_spend(
    individual: Customer,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
    category: MerchantCategory,
) -> None:
    card = cards_by_customer[individual.customer_id][0]
    event = BigPurchase(date(2026, 3, 14), category)
    generator = rng()
    drafts = [
        big_purchase_draft(
            generator, individual, event, card, index, DayBudget(generator, {card.account_id: RICH})
        )
        for _ in range(200)
    ]
    amounts = sorted(d.amount_cents for d in drafts if d is not None)
    assert len(amounts) == 200
    assert amounts[100] > CATEGORY_AMOUNTS[category].median_cents
    for number, draft in enumerate(drafts, start=1):
        assert draft is not None
        to_transaction(draft, number, APPROVED)
        assert to_sast(draft.event_time).date() == event.day


def test_travel_is_booked_with_a_local_online_agency(
    individual: Customer,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
) -> None:
    card = cards_by_customer[individual.customer_id][0]
    event = BigPurchase(date(2026, 3, 14), MerchantCategory.TRAVEL_AGENCY)
    budget = DayBudget(rng(), {card.account_id: RICH})
    draft = big_purchase_draft(rng(), individual, event, card, index, budget)
    assert draft is not None
    assert draft.channel is Channel.CARD_NOT_PRESENT
    assert draft.country_code == "ZA"


def test_unaffordable_big_purchase_is_usually_put_off(
    individual: Customer,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
) -> None:
    card = cards_by_customer[individual.customer_id][0]
    event = BigPurchase(date(2026, 3, 14), MerchantCategory.ONLINE_RETAIL)
    generator = rng()
    made = sum(
        big_purchase_draft(generator, individual, event, card, index, DayBudget(generator, {}))
        is not None
        for _ in range(500)
    )
    assert made < 100


def test_new_phone_is_registered_then_used_to_log_in(individual: Customer) -> None:
    event = NewPhone(date(2026, 4, 2))
    change = register_new_phone(rng(), individual, event, event_ids())

    assert change.device.device_id == "DEV-0000101"
    assert change.link.customer_id == individual.customer_id
    assert change.link.linked_at == change.device.first_seen_at == change.session.started_at
    assert change.session.device_id == change.device.device_id
    assert to_sast(change.session.started_at).date() == event.day


def test_new_payee_is_saved_then_paid_a_large_amount(
    individual: Customer, payment_habits: PaymentHabits
) -> None:
    event = NewPayee(date(2026, 5, 20))
    budget = DayBudget(rng(), {payment_habits.main_account.account_id: RICH})
    change = add_and_pay_new_payee(rng(), individual, event, payment_habits, event_ids(), budget)
    assert change is not None

    payee, created, session, payment = (
        change.beneficiary,
        change.event,
        change.session,
        change.payment,
    )
    assert payee.beneficiary_id == "BEN-0000101"
    assert created.event_type is BeneficiaryEventType.CREATED
    assert created.session_id == session.session_id == payment.debit.session_id
    assert session.started_at < payee.created_at == created.event_time < payment.debit.event_time
    assert payment.debit.beneficiary_id == payee.beneficiary_id
    assert payment.credit is None  # new payees bank elsewhere
    to_transaction(payment.debit, 1, APPROVED)


def test_new_payee_is_skipped_when_unaffordable(
    individual: Customer, payment_habits: PaymentHabits
) -> None:
    event = NewPayee(date(2026, 5, 20))
    generator = rng()
    made = sum(
        add_and_pay_new_payee(
            generator, individual, event, payment_habits, event_ids(), DayBudget(generator, {})
        )
        is not None
        for _ in range(500)
    )
    assert made < 100


def test_same_seed_gives_the_same_plan(individual: Customer) -> None:
    first = plan_unusual_events(rng(), individual, FIRST_DAY, SIX_MONTHS)
    assert first == plan_unusual_events(rng(), individual, FIRST_DAY, SIX_MONTHS)
