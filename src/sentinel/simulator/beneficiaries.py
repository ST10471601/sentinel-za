"""Generate the payees customers saved before the simulation starts."""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sentinel.domain.accounts import Account
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent, BeneficiaryEventType
from sentinel.domain.customers import Customer, CustomerType, IncomeBand
from sentinel.domain.sessions import LoginSession
from sentinel.simulator.identifiers import (
    IdSequence,
    make_account_number,
    make_id,
    make_phone_number,
)
from sentinel.simulator.names import BUSINESS_NAME_WORDS, BUSINESS_TRADES
from sentinel.simulator.randomness import chance, random_datetime_between
from sentinel.simulator.sessions import LinkedDevice, devices_linked_by, start_session

OUR_BANK = "Sentinel Bank"
# Fictional names for the other banks payees use.
OTHER_BANKS: tuple[str, ...] = (
    "Imbali Bank", "Protea Bank", "Karoo Bank", "Baobab Bank", "Umhlaba Bank", "Sable Bank",
)  # fmt: skip

PAYEE_COUNT_BY_BAND: dict[IncomeBand, tuple[int, int]] = {
    IncomeBand.LOW: (1, 4),
    IncomeBand.MIDDLE: (3, 7),
    IncomeBand.HIGH: (5, 10),
    IncomeBand.BUSINESS: (8, 20),
}

# Labels people give saved payees.
PERSONAL_PAYEE_NAMES: tuple[str, ...] = (
    "Mom", "Dad", "Gogo", "Brother", "Sister", "Cousin", "Friend", "Landlord", "Rent",
    "Stokvel", "Creche", "School fees", "Domestic worker", "Garden service", "Bursary",
)  # fmt: skip

# Simulation assumptions: how often a payee banks with us or has a PayShap ID.
INTERNAL_PAYEE_SHARE = 0.25
SAME_CITY_PAYEE_SHARE = 0.6  # of internal payees: family and friends live nearby
SHAP_ID_SHARE = 0.5  # of individuals' payees
MAX_PAYEE_AGE_DAYS = 2 * 365
SESSION_LEAD_SECONDS = (30, 600)  # the payee is added this long after logging in


@dataclass(frozen=True, slots=True)
class PayeeHistory:
    """Saved payees, the events that created them, and the sessions they were added in."""

    beneficiaries: tuple[Beneficiary, ...]
    events: tuple[BeneficiaryEvent, ...]
    sessions: tuple[LoginSession, ...]


def generate_beneficiaries(
    rng: random.Random,
    customers: Sequence[Customer],
    main_accounts: Mapping[str, Account],
    devices_by_customer: Mapping[str, Sequence[LinkedDevice]],
    session_ids: IdSequence,
    simulation_start: datetime,
) -> PayeeHistory:
    """Give each customer a few saved payees, each added in a login session.

    ``main_accounts`` maps customer ID to main account; some payees are other customers,
    which links accounts for the money-flow graph.
    """
    customers_by_city: dict[str, list[Customer]] = {}
    for customer in customers:
        customers_by_city.setdefault(customer.home_city, []).append(customer)

    beneficiaries: list[Beneficiary] = []
    events: list[BeneficiaryEvent] = []
    sessions: list[LoginSession] = []

    for customer in customers:
        linked = devices_by_customer[customer.customer_id]
        first_linked = min(item.linked_at for item in linked)
        for _ in range(rng.randint(*PAYEE_COUNT_BY_BAND[customer.income_band])):
            created_at = _pick_created_at(rng, customer, first_linked, simulation_start)
            started_at = max(
                created_at - timedelta(seconds=rng.randint(*SESSION_LEAD_SECONDS)), first_linked
            )
            devices = devices_linked_by(linked, started_at)
            session = start_session(rng, session_ids, customer, devices, started_at)
            payee_customer = _pick_internal_payee(rng, customer, customers, customers_by_city)
            beneficiary = _make_beneficiary(
                rng,
                beneficiary_id=make_id("BEN", len(beneficiaries) + 1),
                customer=customer,
                payee_account=main_accounts[payee_customer.customer_id] if payee_customer else None,
                payee_customer=payee_customer,
                created_at=created_at,
            )
            beneficiaries.append(beneficiary)
            sessions.append(session)
            events.append(
                BeneficiaryEvent(
                    beneficiary_event_id=make_id("BEV", len(events) + 1),
                    beneficiary_id=beneficiary.beneficiary_id,
                    event_type=BeneficiaryEventType.CREATED,
                    event_time=created_at,
                    session_id=session.session_id,
                    old_account_number=None,
                    new_account_number=beneficiary.payee_account_number,
                )
            )

    return PayeeHistory(tuple(beneficiaries), tuple(events), tuple(sessions))


def _pick_created_at(
    rng: random.Random, customer: Customer, first_linked: datetime, simulation_start: datetime
) -> datetime:
    """A time in the two years before the start, once the customer had a device."""
    earliest = max(
        customer.onboarded_at,
        first_linked,
        simulation_start - timedelta(days=MAX_PAYEE_AGE_DAYS),
    )
    return random_datetime_between(rng, earliest, simulation_start)


def _pick_internal_payee(
    rng: random.Random,
    customer: Customer,
    customers: Sequence[Customer],
    customers_by_city: Mapping[str, Sequence[Customer]],
) -> Customer | None:
    """Sometimes the payee is another of our customers, usually from the same city."""
    if not chance(rng, INTERNAL_PAYEE_SHARE):
        return None
    neighbours = customers_by_city[customer.home_city]
    pool = neighbours if chance(rng, SAME_CITY_PAYEE_SHARE) else customers
    payee = rng.choice(pool)
    return payee if payee is not customer else None


def _make_beneficiary(
    rng: random.Random,
    *,
    beneficiary_id: str,
    customer: Customer,
    payee_account: Account | None,
    payee_customer: Customer | None,
    created_at: datetime,
) -> Beneficiary:
    is_business = customer.customer_type is CustomerType.BUSINESS
    if is_business:
        name = f"{rng.choice(BUSINESS_NAME_WORDS)} {rng.choice(BUSINESS_TRADES)}"
    else:
        name = rng.choice(PERSONAL_PAYEE_NAMES)

    # A payee at our bank uses their real account and phone number, so the payment can
    # be traced to them.
    shap_id = None
    if not is_business and chance(rng, SHAP_ID_SHARE):
        shap_id = payee_customer.phone_number if payee_customer else make_phone_number(rng)

    return Beneficiary(
        beneficiary_id=beneficiary_id,
        customer_id=customer.customer_id,
        beneficiary_name=name,
        payee_bank=OUR_BANK if payee_account else rng.choice(OTHER_BANKS),
        payee_account_number=(
            payee_account.account_number if payee_account else make_account_number(rng)
        ),
        payee_internal_account_id=payee_account.account_id if payee_account else None,
        shap_id=shap_id,
        created_at=created_at,
    )
