"""Fraud fixtures: every customer from the shared reference data, as a potential victim."""

from collections import defaultdict

import pytest

from sentinel.domain.accounts import Account, Card
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.simulator.beneficiaries import PayeeHistory
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.payments import plan_payment_habits
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.sessions import LinkedDevice

MONTHLY_INCOME_CENTS = 2_500_000


@pytest.fixture(scope="session")
def victims(
    reference_data: ReferenceData,
    accounts_by_customer: dict[str, list[Account]],
    cards_by_customer: dict[str, list[Card]],
    devices_by_customer: dict[str, list[LinkedDevice]],
    payees: PayeeHistory,
) -> list[Victim]:
    payees_by_customer: dict[str, list[Beneficiary]] = defaultdict(list)
    for payee in payees.beneficiaries:
        payees_by_customer[payee.customer_id].append(payee)

    return [
        Victim(
            customer,
            tuple(cards_by_customer[customer.customer_id]),
            plan_payment_habits(
                accounts_by_customer[customer.customer_id],
                payees_by_customer[customer.customer_id],
                devices_by_customer[customer.customer_id],
                MONTHLY_INCOME_CENTS,
            ),
        )
        for customer in reference_data.customers
    ]
