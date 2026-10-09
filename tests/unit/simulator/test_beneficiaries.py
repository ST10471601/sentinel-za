import random
from collections import Counter

import pytest

from sentinel.domain.accounts import Account
from sentinel.domain.beneficiaries import BeneficiaryEventType
from sentinel.domain.customers import CustomerType
from sentinel.simulator.beneficiaries import (
    INTERNAL_PAYEE_SHARE,
    OUR_BANK,
    PAYEE_COUNT_BY_BAND,
    PayeeHistory,
    generate_beneficiaries,
)
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.recurring import find_main_account
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData
from sentinel.simulator.sessions import group_devices_by_customer


def generate(reference_data: ReferenceData, seed: int = 3) -> PayeeHistory:
    accounts_by_customer: dict[str, list[Account]] = {}
    for account in reference_data.accounts:
        accounts_by_customer.setdefault(account.customer_id, []).append(account)
    main_accounts = {cid: find_main_account(accts) for cid, accts in accounts_by_customer.items()}

    return generate_beneficiaries(
        random.Random(seed),
        reference_data.customers,
        main_accounts,
        group_devices_by_customer(reference_data.devices, reference_data.customer_devices),
        IdSequence("SES"),
        DEFAULT_SIMULATION_START,
    )


@pytest.fixture(scope="module")
def history(reference_data: ReferenceData) -> PayeeHistory:
    return generate(reference_data)


def test_payee_counts_follow_the_income_band(
    reference_data: ReferenceData, history: PayeeHistory
) -> None:
    counts = Counter(b.customer_id for b in history.beneficiaries)
    for customer in reference_data.customers:
        low, high = PAYEE_COUNT_BY_BAND[customer.income_band]
        assert low <= counts[customer.customer_id] <= high


def test_each_payee_was_created_in_its_own_session_before_the_start(
    history: PayeeHistory,
) -> None:
    session_by_id = {s.session_id: s for s in history.sessions}
    assert len(history.events) == len(history.beneficiaries) == len(session_by_id)

    for beneficiary, event in zip(history.beneficiaries, history.events, strict=True):
        session = session_by_id[event.session_id]
        assert event.beneficiary_id == beneficiary.beneficiary_id
        assert event.event_type is BeneficiaryEventType.CREATED
        assert event.new_account_number == beneficiary.payee_account_number
        assert session.customer_id == beneficiary.customer_id
        assert session.started_at <= event.event_time == beneficiary.created_at
        assert beneficiary.created_at < DEFAULT_SIMULATION_START


def test_sessions_use_devices_already_linked_to_the_customer(
    reference_data: ReferenceData, history: PayeeHistory
) -> None:
    link_time = {
        (link.customer_id, link.device_id): link.linked_at
        for link in reference_data.customer_devices
    }
    for session in history.sessions:
        if session.device_id is not None:
            assert link_time[(session.customer_id, session.device_id)] <= session.started_at


def test_internal_payees_point_at_another_customers_main_account(
    reference_data: ReferenceData, history: PayeeHistory
) -> None:
    account_by_id = {a.account_id: a for a in reference_data.accounts}
    internal = [b for b in history.beneficiaries if b.payee_internal_account_id]

    share = len(internal) / len(history.beneficiaries)
    assert share == pytest.approx(INTERNAL_PAYEE_SHARE, abs=0.05)
    for beneficiary in internal:
        assert beneficiary.payee_internal_account_id is not None
        account = account_by_id[beneficiary.payee_internal_account_id]
        assert account.customer_id != beneficiary.customer_id
        assert account.account_number == beneficiary.payee_account_number
        assert beneficiary.payee_bank == OUR_BANK


def test_external_payees_bank_elsewhere(history: PayeeHistory) -> None:
    external = [b for b in history.beneficiaries if b.payee_internal_account_id is None]
    assert external
    assert all(b.payee_bank != OUR_BANK for b in external)


def test_business_payees_are_suppliers_without_shap_ids(
    reference_data: ReferenceData, history: PayeeHistory
) -> None:
    businesses = {
        c.customer_id for c in reference_data.customers if c.customer_type is CustomerType.BUSINESS
    }
    supplier_payees = [b for b in history.beneficiaries if b.customer_id in businesses]
    assert supplier_payees
    assert all(b.shap_id is None for b in supplier_payees)


def test_ids_are_unique_and_consecutive(history: PayeeHistory) -> None:
    ids = [b.beneficiary_id for b in history.beneficiaries]
    assert ids[0] == "BEN-0000001"
    assert len(set(ids)) == len(ids)
    assert history.events[-1].beneficiary_event_id == f"BEV-{len(history.events):07d}"


def test_same_seed_gives_the_same_payees(reference_data: ReferenceData) -> None:
    assert generate(reference_data, seed=9) == generate(reference_data, seed=9)
