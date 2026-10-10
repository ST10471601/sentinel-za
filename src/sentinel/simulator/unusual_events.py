"""Legitimate but unusual events, so a model can't treat "different" as "fraud".

Each one looks like part of a fraud pattern: a trip (sudden location change), a big
purchase (amount spike), a new phone (device change) and a new payee paid a large amount
(the shape of authorised push payment fraud).
"""

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sentinel.domain.accounts import Card
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent, BeneficiaryEventType
from sentinel.domain.customers import Customer, CustomerType
from sentinel.domain.devices import CustomerDevice, Device, DeviceLinkMethod, DevicePlatform
from sentinel.domain.merchants import MerchantCategory
from sentinel.domain.sessions import LoginSession
from sentinel.simulator.beneficiaries import OTHER_BANKS
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import MerchantIndex, purchase_draft
from sentinel.simulator.devices import IOS_SHARE
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.geography import CITIES, City
from sentinel.simulator.identifiers import (
    IdSequence,
    make_account_number,
    make_device_fingerprint,
)
from sentinel.simulator.payments import PaymentHabits, PlannedPayment, pay_payee
from sentinel.simulator.randomness import chance
from sentinel.simulator.sessions import devices_linked_by, start_session
from sentinel.simulator.spending import (
    CATEGORY_AMOUNTS,
    SPENDING_BY_BAND,
    pick_event_time,
    sample_amount,
    sample_daily_count,
)

# Simulation assumptions: how often each event happens, per customer per month.
TRIP_RATE = 0.12  # individuals only
BIG_PURCHASE_RATE = 0.10
NEW_PHONE_RATE = 0.02  # a new phone every few years
NEW_PAYEE_RATE = 0.12

TRIP_DAYS = (2, 6)
BOOKING_DAYS_AHEAD = (3, 21)  # a trip is booked online this long before leaving

# How many times a normal spend in the category a big purchase is.
BIG_PURCHASE_MULTIPLIER: dict[MerchantCategory, tuple[float, float]] = {
    MerchantCategory.ONLINE_RETAIL: (8.0, 20.0),  # appliances, electronics
    MerchantCategory.CLOTHING: (5.0, 10.0),
}
BOOKING_MULTIPLIER = (1.0, 3.0)  # travel bookings are already large
NEW_PAYEE_MULTIPLIER = (3.0, 10.0)  # a deposit or a once-off bill
NEW_PAYEE_NAMES: tuple[str, ...] = (
    "Car dealer", "Builder", "Attorney", "Furniture store", "Wedding venue",
    "Panel beater", "Movers", "Solar installer", "Driving school", "Vet",
)  # fmt: skip

ADD_PAYEE_DELAY_SECONDS = (30, 300)  # from logging in to saving the payee
PAY_NEW_PAYEE_DELAY_SECONDS = (30, 600)  # from saving the payee to paying it


@dataclass(frozen=True, slots=True)
class Trip:
    """Time away from home in another SA city."""

    start: date
    end: date  # inclusive
    city: City


@dataclass(frozen=True, slots=True)
class BigPurchase:
    """A once-off spend far above the customer's usual amount."""

    day: date
    category: MerchantCategory


@dataclass(frozen=True, slots=True)
class NewPhone:
    """The customer registers a new phone on their profile."""

    day: date


@dataclass(frozen=True, slots=True)
class NewPayee:
    """The customer saves a new payee and pays them a large amount straight away."""

    day: date


type UnusualEvent = Trip | BigPurchase | NewPhone | NewPayee


@dataclass(frozen=True, slots=True)
class PhoneChange:
    """Rows created when a new phone is registered, and the first login on it."""

    device: Device
    link: CustomerDevice
    session: LoginSession


@dataclass(frozen=True, slots=True)
class PayeeChange:
    """Rows created when a new payee is saved and paid."""

    beneficiary: Beneficiary
    event: BeneficiaryEvent
    session: LoginSession
    payment: PlannedPayment


@dataclass(frozen=True, slots=True)
class EventIds:
    """Shared ID sequences, continuing from the reference data."""

    sessions: IdSequence
    devices: IdSequence
    beneficiaries: IdSequence
    beneficiary_events: IdSequence


def plan_unusual_events(
    rng: random.Random, customer: Customer, first_day: date, day_count: int
) -> tuple[UnusualEvent, ...]:
    """Pick which unusual events happen to a customer during the simulation, and when."""
    events: list[UnusualEvent] = []
    is_individual = customer.customer_type is CustomerType.INDIVIDUAL

    if is_individual:
        for _ in range(_count_over_period(rng, TRIP_RATE, day_count)):
            events += _plan_trip(rng, customer, first_day, day_count)
    for _ in range(_count_over_period(rng, BIG_PURCHASE_RATE, day_count)):
        category = rng.choice(sorted(BIG_PURCHASE_MULTIPLIER))
        events.append(BigPurchase(_random_day(rng, first_day, day_count), category))
    for _ in range(_count_over_period(rng, NEW_PHONE_RATE, day_count)):
        events.append(NewPhone(_random_day(rng, first_day, day_count)))
    for _ in range(_count_over_period(rng, NEW_PAYEE_RATE, day_count)):
        events.append(NewPayee(_random_day(rng, first_day, day_count)))
    return tuple(events)


def trip_on(events: tuple[UnusualEvent, ...], day: date) -> Trip | None:
    """Return the trip the customer is on that day, if any."""
    return next(
        (e for e in events if isinstance(e, Trip) and e.start <= day <= e.end),
        None,
    )


def away_customer(customer: Customer, city: City) -> Customer:
    """The customer as seen while travelling: shops, ATMs and app logins are near ``city``."""
    return customer.model_copy(
        update={
            "home_city": city.name,
            "home_province": city.province,
            "home_lat": city.lat,
            "home_lon": city.lon,
        }
    )


def big_purchase_draft(
    rng: random.Random,
    customer: Customer,
    event: BigPurchase,
    card: Card,
    merchants: MerchantIndex,
    budget: DayBudget,
) -> TransactionDraft | None:
    """A large card purchase, or None if the customer can't afford it and holds off."""
    options = merchants.options_for(customer, event.category)
    # Travel is booked with a local agency; foreign agencies are left to fraud scenarios.
    if event.category is MerchantCategory.TRAVEL_AGENCY:
        options = [m for m in options if m.is_online and m.country_code == "ZA"]
        multiplier = BOOKING_MULTIPLIER
    else:
        multiplier = BIG_PURCHASE_MULTIPLIER[event.category]

    scale = SPENDING_BY_BAND[customer.income_band].amount_scale * rng.uniform(*multiplier)
    amount = sample_amount(rng, CATEGORY_AMOUNTS[event.category], scale)
    if not options or not budget.try_spend(card.account_id, amount):
        return None
    contactless_share = SPENDING_BY_BAND[customer.income_band].contactless_share
    return purchase_draft(rng, card, rng.choice(options), amount, event.day, contactless_share)


def register_new_phone(
    rng: random.Random, customer: Customer, event: NewPhone, ids: EventIds
) -> PhoneChange:
    """Register a new smartphone and log in on it."""
    registered_at = pick_event_time(rng, event.day)
    is_ios = chance(rng, IOS_SHARE[customer.income_band])
    device = Device(
        device_id=ids.devices.next_id(),
        device_fingerprint=make_device_fingerprint(rng),
        platform=DevicePlatform.IOS if is_ios else DevicePlatform.ANDROID,
        first_seen_at=registered_at,
    )
    link = CustomerDevice(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        linked_at=registered_at,
        link_method=DeviceLinkMethod.APP_REGISTRATION,
    )
    session = start_session(rng, ids.sessions, customer, [device], registered_at)
    return PhoneChange(device, link, session)


def add_and_pay_new_payee(
    rng: random.Random,
    customer: Customer,
    event: NewPayee,
    habits: PaymentHabits,
    ids: EventIds,
    budget: DayBudget,
) -> PayeeChange | None:
    """Save a new payee and pay them, or None if the customer can't afford it."""
    profile = SPENDING_BY_BAND[customer.income_band]
    amount = round(sample_amount(rng, profile.payment_amount) * rng.uniform(*NEW_PAYEE_MULTIPLIER))
    if not budget.try_spend(habits.main_account.account_id, amount):
        return None

    logged_in_at = pick_event_time(rng, event.day)
    devices = devices_linked_by(habits.devices, logged_in_at)
    session = start_session(rng, ids.sessions, customer, devices, logged_in_at)
    saved_at = _seconds_after(rng, logged_in_at, ADD_PAYEE_DELAY_SECONDS)

    beneficiary = Beneficiary(
        beneficiary_id=ids.beneficiaries.next_id(),
        customer_id=customer.customer_id,
        beneficiary_name=rng.choice(NEW_PAYEE_NAMES),
        payee_bank=rng.choice(OTHER_BANKS),
        payee_account_number=make_account_number(rng),
        payee_internal_account_id=None,
        shap_id=None,
        created_at=saved_at,
    )
    created = BeneficiaryEvent(
        beneficiary_event_id=ids.beneficiary_events.next_id(),
        beneficiary_id=beneficiary.beneficiary_id,
        event_type=BeneficiaryEventType.CREATED,
        event_time=saved_at,
        session_id=session.session_id,
        old_account_number=None,
        new_account_number=beneficiary.payee_account_number,
    )
    paid_at = _seconds_after(rng, saved_at, PAY_NEW_PAYEE_DELAY_SECONDS)
    payment = pay_payee(rng, customer, habits, session, beneficiary, amount, paid_at)
    return PayeeChange(beneficiary, created, session, payment)


def _plan_trip(
    rng: random.Random, customer: Customer, first_day: date, day_count: int
) -> list[UnusualEvent]:
    """A trip to another city, and the booking made online beforehand."""
    destinations = [city for city in CITIES if city.name != customer.home_city]
    city = rng.choices(destinations, weights=[c.weight for c in destinations])[0]
    start = _random_day(rng, first_day, day_count)
    end = start + timedelta(days=rng.randint(*TRIP_DAYS) - 1)

    events: list[UnusualEvent] = [Trip(start, end, city)]
    booked_on = start - timedelta(days=rng.randint(*BOOKING_DAYS_AHEAD))
    if booked_on >= first_day:
        events.append(BigPurchase(booked_on, MerchantCategory.TRAVEL_AGENCY))
    return events


def _count_over_period(rng: random.Random, rate_per_month: float, day_count: int) -> int:
    # A daily count with the whole period as one "day" weighted by its length.
    return sample_daily_count(rng, rate_per_month, weight=day_count)


def _random_day(rng: random.Random, first_day: date, day_count: int) -> date:
    return first_day + timedelta(days=rng.randrange(day_count))


def _seconds_after(rng: random.Random, moment: datetime, seconds: tuple[int, int]) -> datetime:
    return moment + timedelta(seconds=rng.randint(*seconds))
