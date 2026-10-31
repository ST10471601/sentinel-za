"""Genuine changes to saved payees: new bank details and deletions.

A details change followed by a payment is the shape of supplier-mandate fraud, so genuine
changes keep that shape from being fraud-only. Each change is made in its own login session.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent, BeneficiaryEventType
from sentinel.domain.customers import Customer, CustomerType
from sentinel.domain.sessions import LoginSession
from sentinel.simulator.identifiers import make_account_number
from sentinel.simulator.payments import PaymentHabits
from sentinel.simulator.randomness import draw_unique
from sentinel.simulator.sessions import devices_linked_by, start_session
from sentinel.simulator.spending import pick_event_time, sample_daily_count
from sentinel.simulator.unusual_events import ADD_PAYEE_DELAY_SECONDS, EventIds

# Simulation assumptions: changes per saved payee per month. Suppliers move banks or stop being
# used far more often than a relative's bank details change.
DETAILS_CHANGE_RATE: dict[CustomerType, float] = {
    CustomerType.BUSINESS: 0.005,
    CustomerType.INDIVIDUAL: 0.0005,
}
DELETION_RATE: dict[CustomerType, float] = {
    CustomerType.BUSINESS: 0.01,
    CustomerType.INDIVIDUAL: 0.001,
}


@dataclass(frozen=True, slots=True)
class PayeeDetailsChange:
    """The customer gives a saved payee at another bank a new account number."""

    day: date


@dataclass(frozen=True, slots=True)
class PayeeDeletion:
    """The customer removes a saved payee."""

    day: date


@dataclass(frozen=True, slots=True)
class PayeeEdit:
    """Rows created when a saved payee is changed or deleted, and the payees kept after it."""

    event: BeneficiaryEvent
    session: LoginSession
    payees: tuple[Beneficiary, ...]


def plan_payee_changes(
    rng: random.Random,
    customer: Customer,
    payees: Sequence[Beneficiary],
    first_day: date,
    day_count: int,
) -> tuple[PayeeDetailsChange | PayeeDeletion, ...]:
    """Pick the days a customer changes or deletes one of their saved payees."""
    elsewhere = sum(payee.payee_internal_account_id is None for payee in payees)
    change_rate = DETAILS_CHANGE_RATE[customer.customer_type] * elsewhere
    deletion_rate = DELETION_RATE[customer.customer_type] * len(payees)
    changes = sample_daily_count(rng, change_rate, weight=day_count)
    deletions = sample_daily_count(rng, deletion_rate, weight=day_count)
    return (
        *(PayeeDetailsChange(_random_day(rng, first_day, day_count)) for _ in range(changes)),
        *(PayeeDeletion(_random_day(rng, first_day, day_count)) for _ in range(deletions)),
    )


def change_payee_details(
    rng: random.Random, customer: Customer, habits: PaymentHabits, ids: EventIds, day: date
) -> PayeeEdit | None:
    """Give a saved payee at another bank a new account number.

    Later payments carry on to the same payee. Returns None when no payee banks elsewhere.
    """
    elsewhere = [p for p in habits.beneficiaries if p.payee_internal_account_id is None]
    if not elsewhere:
        return None
    payee = rng.choice(elsewhere)
    session, changed_at = _log_in(rng, customer, habits, ids, day)
    old_number = payee.payee_account_number
    new_number = draw_unique(lambda: make_account_number(rng), {old_number})
    event = BeneficiaryEvent(
        beneficiary_event_id=ids.beneficiary_events.next_id(),
        beneficiary_id=payee.beneficiary_id,
        event_type=BeneficiaryEventType.DETAILS_CHANGED,
        event_time=changed_at,
        session_id=session.session_id,
        old_account_number=old_number,
        new_account_number=new_number,
    )
    changed = payee.model_copy(update={"payee_account_number": new_number})
    payees = tuple(
        changed if p.beneficiary_id == payee.beneficiary_id else p for p in habits.beneficiaries
    )
    return PayeeEdit(event, session, payees)


def delete_payee(
    rng: random.Random, customer: Customer, habits: PaymentHabits, ids: EventIds, day: date
) -> PayeeEdit | None:
    """Remove a saved payee, which is never paid again. Returns None when none are left."""
    if not habits.beneficiaries:
        return None
    payee = rng.choice(habits.beneficiaries)
    session, deleted_at = _log_in(rng, customer, habits, ids, day)
    event = BeneficiaryEvent(
        beneficiary_event_id=ids.beneficiary_events.next_id(),
        beneficiary_id=payee.beneficiary_id,
        event_type=BeneficiaryEventType.DELETED,
        event_time=deleted_at,
        session_id=session.session_id,
        old_account_number=payee.payee_account_number,
        new_account_number=None,
    )
    payees = tuple(p for p in habits.beneficiaries if p.beneficiary_id != payee.beneficiary_id)
    return PayeeEdit(event, session, payees)


def _log_in(
    rng: random.Random, customer: Customer, habits: PaymentHabits, ids: EventIds, day: date
) -> tuple[LoginSession, datetime]:
    """A login on ``day``, and the moment the change is saved a little later."""
    logged_in_at = pick_event_time(rng, day)
    devices = devices_linked_by(habits.devices, logged_in_at)
    session = start_session(rng, ids.sessions, customer, devices, logged_in_at)
    saved_at = logged_in_at + timedelta(seconds=rng.randint(*ADD_PAYEE_DELAY_SECONDS))
    return session, saved_at


def _random_day(rng: random.Random, first_day: date, day_count: int) -> date:
    return first_day + timedelta(days=rng.randrange(day_count))
