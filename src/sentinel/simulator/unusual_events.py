"""Legitimate but unusual events, so a model can't treat "different" as "fraud".

Each one looks like part of a fraud pattern: a trip (sudden location change), a big
purchase (amount spike), a new phone or browser (device change), a new payee paid a large
amount (the shape of authorised push payment fraud) and a SIM swap, often followed by a new
device (the start of an account takeover).
"""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.domain.accounts import Card
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent, BeneficiaryEventType
from sentinel.domain.customers import BankingChannel, Customer, CustomerType
from sentinel.domain.devices import CustomerDevice, Device, DevicePlatform
from sentinel.domain.merchants import MerchantCategory
from sentinel.domain.sessions import LoginSession
from sentinel.domain.sim_swaps import SimSwapEvent
from sentinel.simulator.beneficiaries import OTHER_BANKS
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import MerchantIndex, purchase_draft
from sentinel.simulator.devices import IOS_SHARE, LINK_METHOD_BY_PLATFORM
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.geography import CITIES, City
from sentinel.simulator.identifiers import (
    IdSequence,
    make_account_number,
    make_device_fingerprint,
)
from sentinel.simulator.payments import PaymentHabits, PlannedPayment, pay_payee
from sentinel.simulator.randomness import chance, random_datetime_between
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
SIM_SWAP_RATE = 0.01  # lost, stolen or broken phones and SIM upgrades
NEW_BROWSER_RATE = 0.05  # customers who already bank in a browser: new laptop, cleared cookies

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

# Simulation assumptions for SIM swaps. Half are followed by a new device, registered with an
# OTP sent to the new SIM: a browser for customers who bank mainly in one, else a phone. The
# rest move the new SIM into a phone that is already on the profile.
SIM_SWAP_NEW_DEVICE_SHARE = 0.5
NEW_DEVICE_AFTER_SWAP_DAYS = (0, 3)
DEVICE_SETUP_SECONDS = (10 * 60, 120 * 60)  # from getting the new SIM to registering the device
STORE_SWAP_SHARE = 0.85  # the rest go through a call centre or an eSIM in the network's app
STORE_HOURS: tuple[tuple[int, int], ...] = ((8, 18),) * 5 + ((8, 15), (9, 13))  # Monday first
# Fraud uses the same delay, so the gap before the bank hears of a swap says nothing alone.
SIM_SWAP_NOTICE_SECONDS = (1 * 3600, 6 * 3600)

# Share of SA mobile subscribers per network, largest first, from the four networks' own counts
# (late 2024 to mid 2025: 50.7m, 39.2m, 24m and 7.7m).
NETWORK_SHARES: dict[str, float] = {"MNO-1": 0.42, "MNO-2": 0.32, "MNO-3": 0.20, "MNO-4": 0.06}


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
    not_before: datetime | None = None  # after a SIM swap, the registration OTP needs the new SIM


@dataclass(frozen=True, slots=True)
class NewBrowser:
    """The customer links a new browser to their profile for internet banking."""

    day: date
    not_before: datetime | None = None  # after a SIM swap, the OTP needs the new SIM


@dataclass(frozen=True, slots=True)
class NewPayee:
    """The customer saves a new payee and pays them a large amount straight away."""

    day: date


@dataclass(frozen=True, slots=True)
class SimSwap:
    """The customer's number moves to a new SIM card."""

    swapped_at: datetime

    @property
    def day(self) -> date:
        """The SA calendar day of the swap."""
        return to_sast(self.swapped_at).date()


type UnusualEvent = Trip | BigPurchase | NewPhone | NewBrowser | NewPayee | SimSwap


@dataclass(frozen=True, slots=True)
class DeviceChange:
    """Rows created when a device is registered, and the first login on it."""

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
    sim_swaps: IdSequence


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


def plan_sim_swaps(
    rng: random.Random, customer: Customer, first_day: date, day_count: int
) -> tuple[UnusualEvent, ...]:
    """Pick when a customer's SIM is swapped, and whether a new device is registered after."""
    last_day = first_day + timedelta(days=day_count - 1)
    in_browser = customer.preferred_channel is BankingChannel.INTERNET
    events: list[UnusualEvent] = []
    for _ in range(_count_over_period(rng, SIM_SWAP_RATE, day_count)):
        swap = SimSwap(_pick_swap_time(rng, _random_day(rng, first_day, day_count)))
        events.append(swap)
        if not chance(rng, SIM_SWAP_NEW_DEVICE_SHARE):
            continue
        device_day = swap.day + timedelta(days=rng.randint(*NEW_DEVICE_AFTER_SWAP_DAYS))
        if device_day > last_day:
            continue
        if in_browser:
            events.append(NewBrowser(device_day, not_before=swap.swapped_at))
        else:
            events.append(NewPhone(device_day, not_before=swap.swapped_at))
    return tuple(events)


def plan_new_browsers(
    rng: random.Random, first_day: date, day_count: int
) -> tuple[UnusualEvent, ...]:
    """Pick the days a customer who banks in a browser links a new one."""
    count = _count_over_period(rng, NEW_BROWSER_RATE, day_count)
    return tuple(NewBrowser(_random_day(rng, first_day, day_count)) for _ in range(count))


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


def register_device(
    rng: random.Random,
    ids: EventIds,
    customer: Customer,
    platform: DevicePlatform,
    registered_at: datetime,
) -> DeviceChange:
    """Link a device to the customer's profile the way its platform always is, and log in on it."""
    device = Device(
        device_id=ids.devices.next_id(),
        device_fingerprint=make_device_fingerprint(rng),
        platform=platform,
        first_seen_at=registered_at,
    )
    link = CustomerDevice(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        linked_at=registered_at,
        link_method=LINK_METHOD_BY_PLATFORM[platform],
    )
    session = start_session(rng, ids.sessions, customer, [device], registered_at)
    return DeviceChange(device, link, session)


def register_new_phone(
    rng: random.Random, customer: Customer, event: NewPhone, ids: EventIds
) -> DeviceChange:
    """Register a new smartphone and log in on it."""
    registered_at = _registration_time(rng, event.day, event.not_before)
    is_ios = chance(rng, IOS_SHARE[customer.income_band])
    platform = DevicePlatform.IOS if is_ios else DevicePlatform.ANDROID
    return register_device(rng, ids, customer, platform, registered_at)


def register_new_browser(
    rng: random.Random, customer: Customer, event: NewBrowser, ids: EventIds
) -> DeviceChange:
    """Link a new browser and log in to internet banking on it."""
    registered_at = _registration_time(rng, event.day, event.not_before)
    return register_device(rng, ids, customer, DevicePlatform.WEB, registered_at)


def notify_sim_swap(
    rng: random.Random,
    sim_swap_ids: IdSequence,
    customer: Customer,
    network: str,
    swapped_at: datetime,
) -> SimSwapEvent:
    """The network's notice to the bank that the customer's number moved to a new SIM."""
    return SimSwapEvent(
        sim_swap_id=sim_swap_ids.next_id(),
        customer_id=customer.customer_id,
        phone_number=customer.phone_number,
        swapped_at=swapped_at,
        notified_at=_seconds_after(rng, swapped_at, SIM_SWAP_NOTICE_SECONDS),
        network=network,
    )


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


def _registration_time(rng: random.Random, day: date, not_before: datetime | None) -> datetime:
    """A usual time on ``day``, moved after a SIM swap if it would come before it."""
    registered_at = pick_event_time(rng, day)
    if not_before is not None and registered_at < not_before:
        return _seconds_after(rng, not_before, DEVICE_SETUP_SECONDS)
    return registered_at


def _pick_swap_time(rng: random.Random, day: date) -> datetime:
    """A time in network store hours, or at any hour by phone or app."""
    opens, closes = STORE_HOURS[day.weekday()] if chance(rng, STORE_SWAP_SHARE) else (0, 24)
    start = datetime.combine(day, time(opens), tzinfo=SAST)
    end = start + timedelta(hours=closes - opens)
    return random_datetime_between(rng, start, end).astimezone(UTC)


def _count_over_period(rng: random.Random, rate_per_month: float, day_count: int) -> int:
    # A daily count with the whole period as one "day" weighted by its length.
    return sample_daily_count(rng, rate_per_month, weight=day_count)


def _random_day(rng: random.Random, first_day: date, day_count: int) -> date:
    return first_day + timedelta(days=rng.randrange(day_count))


def _seconds_after(rng: random.Random, moment: datetime, seconds: tuple[int, int]) -> datetime:
    return moment + timedelta(seconds=rng.randint(*seconds))
