import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Account, AccountStatus, AccountType, Card
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEventType
from sentinel.domain.customers import BankingChannel, Customer, CustomerType
from sentinel.domain.devices import DeviceLinkMethod, DevicePlatform
from sentinel.domain.merchants import MerchantCategory
from sentinel.domain.transactions import Channel, TransactionStatus
from sentinel.simulator.beneficiaries import PERSONAL_PAYEE_NAMES, SHAP_ID_SHARE, PayeeHistory
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import MerchantIndex, plan_card_habits
from sentinel.simulator.drafts import Settlement, to_transaction
from sentinel.simulator.geography import CITIES
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import PaymentHabits, plan_payment_habits
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.sessions import LinkedDevice
from sentinel.simulator.spending import CATEGORY_AMOUNTS, SPENDING_BY_BAND
from sentinel.simulator.unusual_events import (
    BOOKING_DAYS_AHEAD,
    DEVICE_SETUP_SECONDS,
    NEW_DEVICE_AFTER_SWAP_DAYS,
    NEW_PAYEE_NAMES,
    NEW_PERSON_PAYEE_RATE,
    SIM_SWAP_NOTICE_SECONDS,
    STORE_HOURS,
    TRIP_DAYS,
    TWO_PEOPLE_SHARE,
    BigPurchase,
    EventIds,
    NewBrowser,
    NewPayee,
    NewPhone,
    PayeeChange,
    SimSwap,
    Trip,
    UnusualEvent,
    add_and_pay_new_payee,
    away_customer,
    big_purchase_draft,
    notify_sim_swap,
    plan_new_browsers,
    plan_person_payees,
    plan_sim_swaps,
    plan_unusual_events,
    register_device,
    register_new_browser,
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
        sim_swaps=IdSequence("SIM"),
    )


def transfer_groups() -> IdSequence:
    return IdSequence("TRF", digits=9)


def pay_new_person(
    generator: random.Random, customer: Customer, habits: PaymentHabits, budget: DayBudget
) -> PayeeChange | None:
    event = NewPayee(date(2026, 5, 20), is_person=True)
    ids, groups = event_ids(), transfer_groups()
    return add_and_pay_new_payee(generator, customer, event, habits, ids, budget, groups)


@pytest.fixture(scope="module")
def individual(reference_data: ReferenceData) -> Customer:
    return next(c for c in reference_data.customers if c.customer_type is CustomerType.INDIVIDUAL)


def customer_banking_on(reference_data: ReferenceData, channel: BankingChannel) -> Customer:
    return next(
        c
        for c in reference_data.customers
        if c.customer_type is CustomerType.INDIVIDUAL and c.preferred_channel is channel
    )


@pytest.fixture(scope="module")
def plans(reference_data: ReferenceData) -> Plans:
    generator = rng()
    return [
        (customer, plan_unusual_events(generator, customer, FIRST_DAY, SIX_MONTHS))
        for customer in reference_data.customers
    ]


@pytest.fixture(scope="module")
def swap_plans(reference_data: ReferenceData) -> list[tuple[UnusualEvent, ...]]:
    """An app user's swaps, so the new device after a swap is a phone."""
    app_user = customer_banking_on(reference_data, BankingChannel.APP)
    generator = rng()
    return [plan_sim_swaps(generator, app_user, FIRST_DAY, SIX_MONTHS) for _ in range(20_000)]


def swaps_in(plans: list[tuple[UnusualEvent, ...]]) -> list[SimSwap]:
    return [event for events in plans for event in events if isinstance(event, SimSwap)]


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


@pytest.fixture(scope="module")
def habits_with_reserves(
    payment_habits: PaymentHabits, reference_data: ReferenceData
) -> PaymentHabits:
    """The individual's habits with a savings account and credit card that allow big transfers."""

    def first_active(account_type: AccountType) -> Account:
        account = next(
            a
            for a in reference_data.accounts
            if a.account_type is account_type and a.status is AccountStatus.ACTIVE
        )
        return account.model_copy(update={"daily_transfer_limit_cents": RICH})

    return replace(
        payment_habits,
        savings_account=first_active(AccountType.SAVINGS),
        credit_account=first_active(AccountType.CREDIT_CARD),
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
    change = add_and_pay_new_payee(
        rng(), individual, event, payment_habits, event_ids(), budget, transfer_groups()
    )
    assert change is not None

    (payee,), (created,), session, (payment,) = (
        change.beneficiaries,
        change.events,
        change.session,
        change.payments,
    )
    assert payee.beneficiary_id == "BEN-0000101"
    assert payee.beneficiary_name in NEW_PAYEE_NAMES
    assert payee.shap_id is None
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
            generator,
            individual,
            event,
            payment_habits,
            event_ids(),
            DayBudget(generator, {}),
            transfer_groups(),
        )
        is not None
        for _ in range(500)
    )
    assert made < 100


def test_a_short_main_account_is_topped_up_from_savings_then_credit_in_the_same_session(
    individual: Customer, habits_with_reserves: PaymentHabits
) -> None:
    habits = habits_with_reserves
    assert habits.savings_account is not None
    assert habits.credit_account is not None
    main_id = habits.main_account.account_id
    savings_id, credit_id = habits.savings_account.account_id, habits.credit_account.account_id
    generator = rng()

    for _ in range(50):
        budget = DayBudget(generator, {main_id: 0, savings_id: 10_050, credit_id: RICH})
        event = NewPayee(date(2026, 5, 20))
        ids, groups = event_ids(), transfer_groups()
        change = add_and_pay_new_payee(generator, individual, event, habits, ids, budget, groups)
        assert change is not None
        *moves, payment = change.payments
        sources = [move.debit.account_id for move in moves]
        assert sources == [savings_id, credit_id]  # savings first, then credit
        assert {move.debit.channel for move in moves} == {Channel.INTERNAL_TRANSFER}
        assert {move.debit.session_id for move in change.payments} == {change.session.session_id}
        assert moves[0].debit.amount_cents == 10_000  # all savings had, in round R100s
        moved = sum(move.debit.amount_cents for move in moves)
        assert payment.debit.amount_cents <= moved < payment.debit.amount_cents + 10_000
        assert moves[-1].debit.event_time < change.beneficiaries[0].created_at


def test_a_new_person_is_paid_a_usual_amount_and_sometimes_has_a_shap_id(
    individual: Customer, payment_habits: PaymentHabits
) -> None:
    generator = rng()
    budget = DayBudget(generator, {payment_habits.main_account.account_id: RICH})
    changes = [pay_new_person(generator, individual, payment_habits, budget) for _ in range(1_000)]
    payees = [payee for change in changes if change for payee in change.beneficiaries]
    amounts = [p.debit.amount_cents for change in changes if change for p in change.payments]
    sessions = Counter(len(change.beneficiaries) for change in changes if change)

    assert all(payee.beneficiary_name in PERSONAL_PAYEE_NAMES for payee in payees)
    assert sum(payee.shap_id is not None for payee in payees) / len(payees) == pytest.approx(
        SHAP_ID_SHARE, abs=0.05
    )
    assert sessions[2] / len(changes) == pytest.approx(TWO_PEOPLE_SHARE, abs=0.03)
    assert set(sessions) == {1, 2}
    usual = SPENDING_BY_BAND[individual.income_band].payment_amount.median_cents
    assert sorted(amounts)[len(amounts) // 2] < 2 * usual  # not a large once-off bill
    assert all(amount % 100 == 0 for amount in amounts)  # people type whole rands


def test_two_new_people_are_saved_before_either_is_paid(
    individual: Customer, payment_habits: PaymentHabits
) -> None:
    generator = rng()
    budget = DayBudget(generator, {payment_habits.main_account.account_id: RICH})
    for _ in range(200):
        change = pay_new_person(generator, individual, payment_habits, budget)
        assert change is not None
        if len(change.beneficiaries) < 2:
            continue
        first, second = change.beneficiaries
        assert first.beneficiary_name != second.beneficiary_name
        assert second.created_at < change.payments[0].debit.event_time
        paid = [payment.debit.beneficiary_id for payment in change.payments]
        assert paid == [first.beneficiary_id, second.beneficiary_id]
        return
    pytest.fail("no change saved two people")


def test_only_individuals_save_new_people_at_the_monthly_rate(
    reference_data: ReferenceData,
) -> None:
    generator = rng()
    plans = [
        (customer, plan_person_payees(generator, customer, FIRST_DAY, SIX_MONTHS))
        for customer in reference_data.customers
        for _ in range(5)
    ]
    individuals = [events for c, events in plans if c.customer_type is CustomerType.INDIVIDUAL]
    businesses = [events for c, events in plans if c.customer_type is CustomerType.BUSINESS]

    assert businesses
    assert not any(businesses)
    count = sum(len(events) for events in individuals)
    assert count / len(individuals) / (SIX_MONTHS / 30.44) == pytest.approx(
        NEW_PERSON_PAYEE_RATE, abs=0.005
    )
    for events in individuals:
        for event in events:
            assert isinstance(event, NewPayee)
            assert event.is_person


def test_same_seed_gives_the_same_plan(individual: Customer) -> None:
    first = plan_unusual_events(rng(), individual, FIRST_DAY, SIX_MONTHS)
    assert first == plan_unusual_events(rng(), individual, FIRST_DAY, SIX_MONTHS)


def test_sim_swaps_follow_the_monthly_rate(swap_plans: list[tuple[UnusualEvent, ...]]) -> None:
    months = SIX_MONTHS / 30.44
    rate = len(swaps_in(swap_plans)) / len(swap_plans) / months
    assert rate == pytest.approx(0.01, abs=0.002)


def test_about_half_of_sim_swaps_bring_a_new_phone(
    swap_plans: list[tuple[UnusualEvent, ...]],
) -> None:
    phones = [e for events in swap_plans for e in events if isinstance(e, NewPhone)]
    assert len(phones) / len(swaps_in(swap_plans)) == pytest.approx(0.5, abs=0.06)


def test_a_new_phone_comes_0_to_3_days_after_its_sim_swap(
    swap_plans: list[tuple[UnusualEvent, ...]],
) -> None:
    for events in swap_plans:
        swap_by_time = {e.swapped_at: e for e in events if isinstance(e, SimSwap)}
        for phone in (e for e in events if isinstance(e, NewPhone)):
            assert phone.not_before is not None
            swap = swap_by_time[phone.not_before]
            low, high = NEW_DEVICE_AFTER_SWAP_DAYS
            assert low <= (phone.day - swap.day).days <= high
            assert phone.day <= FIRST_DAY + timedelta(days=SIX_MONTHS - 1)


def test_most_sim_swaps_happen_in_store_hours(swap_plans: list[tuple[UnusualEvent, ...]]) -> None:
    def in_store_hours(swap: SimSwap) -> bool:
        local = to_sast(swap.swapped_at)
        opens, closes = STORE_HOURS[local.weekday()]
        return opens <= local.hour < closes

    swaps = swaps_in(swap_plans)
    share = sum(in_store_hours(swap) for swap in swaps) / len(swaps)
    # 85% go to a store; some phone and app swaps also land in store hours.
    assert 0.85 < share < 0.97


def test_bank_hears_of_a_sim_swap_hours_later(individual: Customer) -> None:
    swapped_at = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)
    ids = IdSequence("SIM")
    generator = rng()
    swaps = [notify_sim_swap(generator, ids, individual, "MNO-2", swapped_at) for _ in range(200)]

    assert swaps[0].sim_swap_id == "SIM-0000001"
    assert {(s.customer_id, s.phone_number, s.network) for s in swaps} == {
        (individual.customer_id, individual.phone_number, "MNO-2")
    }
    lags = [(s.notified_at - s.swapped_at).total_seconds() for s in swaps]
    assert SIM_SWAP_NOTICE_SECONDS[0] <= min(lags) <= max(lags) <= SIM_SWAP_NOTICE_SECONDS[1]


def test_a_phone_after_a_sim_swap_is_registered_after_the_swap(individual: Customer) -> None:
    day = date(2026, 4, 2)
    late_swap = datetime(2026, 4, 2, 20, 0, tzinfo=UTC)  # 22:00 in SA
    early_swap = datetime(2026, 4, 1, 22, 1, tzinfo=UTC)  # a minute past midnight in SA

    generator = rng()
    for _ in range(50):
        late = register_new_phone(generator, individual, NewPhone(day, late_swap), event_ids())
        setup_seconds = (late.link.linked_at - late_swap).total_seconds()
        assert 0 <= setup_seconds <= DEVICE_SETUP_SECONDS[1]

    early = register_new_phone(rng(), individual, NewPhone(day, early_swap), event_ids())
    plain = register_new_phone(rng(), individual, NewPhone(day), event_ids())
    assert early.link.linked_at == plain.link.linked_at  # a later usual time is kept


def test_browser_users_link_a_new_browser_after_a_sim_swap(reference_data: ReferenceData) -> None:
    browser_user = customer_banking_on(reference_data, BankingChannel.INTERNET)
    generator = rng()
    events = [
        event
        for _ in range(2_000)
        for event in plan_sim_swaps(generator, browser_user, FIRST_DAY, SIX_MONTHS)
    ]

    browsers = [e for e in events if isinstance(e, NewBrowser)]
    assert browsers
    assert not any(isinstance(e, NewPhone) for e in events)
    swap_times = {e.swapped_at for e in events if isinstance(e, SimSwap)}
    assert all(browser.not_before in swap_times for browser in browsers)


def test_new_browsers_follow_the_monthly_rate() -> None:
    generator = rng()
    plans = [plan_new_browsers(generator, FIRST_DAY, SIX_MONTHS) for _ in range(5_000)]
    browsers = [event for events in plans for event in events]

    assert len(browsers) / len(plans) / (SIX_MONTHS / 30.44) == pytest.approx(0.05, abs=0.005)
    last_day = FIRST_DAY + timedelta(days=SIX_MONTHS - 1)
    for browser in browsers:
        assert isinstance(browser, NewBrowser)
        assert FIRST_DAY <= browser.day <= last_day
        assert browser.not_before is None


def test_new_browser_is_linked_by_otp_then_used_for_internet_banking(
    individual: Customer,
) -> None:
    event = NewBrowser(date(2026, 4, 2))
    change = register_new_browser(rng(), individual, event, event_ids())

    assert change.device.platform is DevicePlatform.WEB
    assert change.link.link_method is DeviceLinkMethod.OTP
    assert change.link.linked_at == change.device.first_seen_at == change.session.started_at
    assert change.session.channel is BankingChannel.INTERNET
    assert change.session.device_id == change.device.device_id
    assert (change.session.ip_country, change.session.lat) == (
        "ZA",
        None,
    )  # browsers send no location
    assert to_sast(change.session.started_at).date() == event.day


@pytest.mark.parametrize(
    ("platform", "link_method"),
    [
        (DevicePlatform.ANDROID, DeviceLinkMethod.APP_REGISTRATION),
        (DevicePlatform.IOS, DeviceLinkMethod.APP_REGISTRATION),
        (DevicePlatform.WEB, DeviceLinkMethod.OTP),
    ],
)
def test_every_registration_links_a_platform_the_same_way(
    individual: Customer, platform: DevicePlatform, link_method: DeviceLinkMethod
) -> None:
    registered_at = datetime(2026, 4, 2, 9, 0, tzinfo=UTC)
    change = register_device(rng(), event_ids(), individual, platform, registered_at)

    assert change.device.platform is platform
    assert change.link.link_method is link_method
    assert change.session.started_at == registered_at
