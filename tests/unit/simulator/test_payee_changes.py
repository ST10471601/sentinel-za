import random
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Account
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEventType
from sentinel.domain.customers import Customer, CustomerType
from sentinel.simulator.beneficiaries import PayeeHistory
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payee_changes import (
    DELETION_RATE,
    DETAILS_CHANGE_RATE,
    PayeeDeletion,
    PayeeDetailsChange,
    change_payee_details,
    delete_payee,
    plan_payee_changes,
)
from sentinel.simulator.payments import PaymentHabits, plan_payment_habits
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.sessions import LinkedDevice
from sentinel.simulator.unusual_events import ADD_PAYEE_DELAY_SECONDS, EventIds

FIRST_DAY = date(2026, 1, 1)
SIX_MONTHS = 181
DAY = date(2026, 2, 10)


def event_ids() -> EventIds:
    return EventIds(
        sessions=IdSequence("SES", start_after=100),
        devices=IdSequence("DEV", start_after=100),
        beneficiaries=IdSequence("BEN", start_after=100),
        beneficiary_events=IdSequence("BEV", start_after=100),
        sim_swaps=IdSequence("SIM"),
    )


def elsewhere(payees: tuple[Beneficiary, ...] | list[Beneficiary]) -> list[Beneficiary]:
    return [payee for payee in payees if payee.payee_internal_account_id is None]


@pytest.fixture(scope="module")
def payees_by_customer(payees: PayeeHistory) -> dict[str, list[Beneficiary]]:
    grouped: dict[str, list[Beneficiary]] = defaultdict(list)
    for payee in payees.beneficiaries:
        grouped[payee.customer_id].append(payee)
    return grouped


@pytest.fixture(scope="module")
def business(
    reference_data: ReferenceData,
    accounts_by_customer: dict[str, list[Account]],
    devices_by_customer: dict[str, list[LinkedDevice]],
    payees_by_customer: dict[str, list[Beneficiary]],
) -> tuple[Customer, PaymentHabits]:
    """A business with suppliers at other banks."""
    customer = next(
        c
        for c in reference_data.customers
        if c.customer_type is CustomerType.BUSINESS
        and len(elsewhere(payees_by_customer[c.customer_id])) >= 2
    )
    habits = plan_payment_habits(
        accounts_by_customer[customer.customer_id],
        payees_by_customer[customer.customer_id],
        devices_by_customer[customer.customer_id],
        monthly_income_cents=20_000_000,
    )
    return customer, habits


def test_changes_and_deletions_follow_the_monthly_rates_per_payee(
    reference_data: ReferenceData, payees_by_customer: dict[str, list[Beneficiary]]
) -> None:
    generator = random.Random(3)
    months = SIX_MONTHS / 30.44
    events: Counter[tuple[CustomerType, type]] = Counter()
    exposure: Counter[tuple[CustomerType, type]] = Counter()
    for _ in range(40):
        for customer in reference_data.customers:
            payees = payees_by_customer[customer.customer_id]
            kind = customer.customer_type
            for event in plan_payee_changes(generator, customer, payees, FIRST_DAY, SIX_MONTHS):
                events[kind, type(event)] += 1
            exposure[kind, PayeeDetailsChange] += len(elsewhere(payees))
            exposure[kind, PayeeDeletion] += len(payees)

    for kind in CustomerType:
        for event_type, rates in (
            (PayeeDetailsChange, DETAILS_CHANGE_RATE),
            (PayeeDeletion, DELETION_RATE),
        ):
            rate = events[kind, event_type] / exposure[kind, event_type] / months
            assert rate == pytest.approx(rates[kind], rel=0.15)


def test_planned_changes_fall_inside_the_simulation(
    business: tuple[Customer, PaymentHabits],
) -> None:
    customer, habits = business
    generator = random.Random(4)
    last_day = FIRST_DAY + timedelta(days=SIX_MONTHS - 1)
    for _ in range(200):
        plan = plan_payee_changes(generator, customer, habits.beneficiaries, FIRST_DAY, SIX_MONTHS)
        assert all(FIRST_DAY <= event.day <= last_day for event in plan)


def test_a_details_change_gives_a_payee_elsewhere_a_new_number_in_its_own_login(
    business: tuple[Customer, PaymentHabits],
) -> None:
    customer, habits = business
    edit = change_payee_details(random.Random(5), customer, habits, event_ids(), DAY)
    assert edit is not None
    event, session = edit.event, edit.session

    before = {payee.beneficiary_id: payee for payee in habits.beneficiaries}
    payee = before[event.beneficiary_id]
    assert payee.payee_internal_account_id is None
    assert event.event_type is BeneficiaryEventType.DETAILS_CHANGED
    assert event.old_account_number == payee.payee_account_number != event.new_account_number
    assert (session.session_id, session.customer_id) == (event.session_id, customer.customer_id)
    assert event.beneficiary_event_id == "BEV-0000101"
    delay = (event.event_time - session.started_at).total_seconds()
    assert ADD_PAYEE_DELAY_SECONDS[0] <= delay <= ADD_PAYEE_DELAY_SECONDS[1]
    assert to_sast(session.started_at).date() == DAY

    after = {p.beneficiary_id: p for p in edit.payees}
    assert list(after) == list(before)  # the payee stays and is still paid
    assert after[payee.beneficiary_id].payee_account_number == event.new_account_number
    unchanged = set(before) - {payee.beneficiary_id}
    assert all(after[bid] == before[bid] for bid in unchanged)


def test_a_deleted_payee_is_no_longer_a_regular_payee(
    business: tuple[Customer, PaymentHabits],
) -> None:
    customer, habits = business
    edit = delete_payee(random.Random(6), customer, habits, event_ids(), DAY)
    assert edit is not None
    event = edit.event

    deleted = next(p for p in habits.beneficiaries if p.beneficiary_id == event.beneficiary_id)
    assert event.event_type is BeneficiaryEventType.DELETED
    assert (event.old_account_number, event.new_account_number) == (
        deleted.payee_account_number,
        None,
    )
    assert event.session_id == edit.session.session_id
    assert edit.payees == tuple(p for p in habits.beneficiaries if p is not deleted)


def test_a_changed_payee_is_deleted_with_its_new_number(
    business: tuple[Customer, PaymentHabits],
) -> None:
    customer, habits = business
    supplier = elsewhere(habits.beneficiaries)[0]
    one_supplier = replace(habits, beneficiaries=(supplier,))
    generator, ids = random.Random(7), event_ids()

    changed = change_payee_details(generator, customer, one_supplier, ids, DAY)
    assert changed is not None
    deleted = delete_payee(
        generator, customer, replace(habits, beneficiaries=changed.payees), ids, DAY
    )
    assert deleted is not None
    assert deleted.event.old_account_number == changed.event.new_account_number
    assert deleted.payees == ()


def test_nothing_is_changed_without_a_payee_to_change(
    business: tuple[Customer, PaymentHabits],
) -> None:
    customer, habits = business
    at_our_bank = tuple(p for p in habits.beneficiaries if p.payee_internal_account_id)
    generator = random.Random(8)

    assert (
        change_payee_details(
            generator, customer, replace(habits, beneficiaries=at_our_bank), event_ids(), DAY
        )
        is None
    )
    assert (
        delete_payee(generator, customer, replace(habits, beneficiaries=()), event_ids(), DAY)
        is None
    )
