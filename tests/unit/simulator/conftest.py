"""Shared simulator fixtures, built once per test session from the same reference data."""

import random
from collections import defaultdict

import pytest

from sentinel.domain.accounts import Account, Card
from sentinel.simulator.beneficiaries import PayeeHistory, generate_beneficiaries
from sentinel.simulator.card_activity import MerchantIndex
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.recurring import find_main_account
from sentinel.simulator.reference_data import (
    DEFAULT_SIMULATION_START,
    ReferenceData,
    generate_reference_data,
)
from sentinel.simulator.sessions import LinkedDevice, group_devices_by_customer

SEED = 7


@pytest.fixture(scope="session")
def reference_data() -> ReferenceData:
    """One default-sized simulation shared by the read-only tests."""
    return generate_reference_data(SEED)


@pytest.fixture(scope="session")
def accounts_by_customer(reference_data: ReferenceData) -> dict[str, list[Account]]:
    grouped: dict[str, list[Account]] = defaultdict(list)
    for account in reference_data.accounts:
        grouped[account.customer_id].append(account)
    return grouped


@pytest.fixture(scope="session")
def cards_by_customer(reference_data: ReferenceData) -> dict[str, list[Card]]:
    owner = {account.account_id: account.customer_id for account in reference_data.accounts}
    grouped: dict[str, list[Card]] = defaultdict(list)
    for card in reference_data.cards:
        grouped[owner[card.account_id]].append(card)
    return grouped


@pytest.fixture(scope="session")
def devices_by_customer(reference_data: ReferenceData) -> dict[str, list[LinkedDevice]]:
    return group_devices_by_customer(reference_data.devices, reference_data.customer_devices)


@pytest.fixture(scope="session")
def index(reference_data: ReferenceData) -> MerchantIndex:
    return MerchantIndex(reference_data.merchants)


@pytest.fixture(scope="session")
def payees(
    reference_data: ReferenceData,
    accounts_by_customer: dict[str, list[Account]],
    devices_by_customer: dict[str, list[LinkedDevice]],
) -> PayeeHistory:
    main_accounts = {cid: find_main_account(accts) for cid, accts in accounts_by_customer.items()}
    return generate_beneficiaries(
        random.Random(2),
        reference_data.customers,
        main_accounts,
        devices_by_customer,
        IdSequence("SES"),
        DEFAULT_SIMULATION_START,
    )
