import pytest

from sentinel.domain.accounts import Account, AccountType
from sentinel.domain.customers import Customer, CustomerType
from sentinel.simulator.accounts import CARD_TYPE_BY_ACCOUNT_TYPE, MAX_CREDIT_USED
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData

START = DEFAULT_SIMULATION_START


@pytest.fixture(scope="module")
def customers_by_id(reference_data: ReferenceData) -> dict[str, Customer]:
    return {customer.customer_id: customer for customer in reference_data.customers}


def test_every_customer_has_one_main_account_opened_when_they_joined(
    reference_data: ReferenceData, accounts_by_customer: dict[str, list[Account]]
) -> None:
    for customer in reference_data.customers:
        accounts = accounts_by_customer[customer.customer_id]
        expected = (
            AccountType.BUSINESS_CURRENT
            if customer.customer_type is CustomerType.BUSINESS
            else AccountType.CHEQUE
        )
        assert accounts[0].account_type is expected
        assert accounts[0].opened_at == customer.onboarded_at
        assert [a.account_type for a in accounts].count(expected) == 1


def test_accounts_belong_to_known_customers_and_open_before_the_start(
    reference_data: ReferenceData, customers_by_id: dict[str, Customer]
) -> None:
    for account in reference_data.accounts:
        customer = customers_by_id[account.customer_id]
        assert customer.onboarded_at <= account.opened_at < START


def test_account_numbers_are_unique(reference_data: ReferenceData) -> None:
    numbers = [account.account_number for account in reference_data.accounts]
    assert len(numbers) == len(set(numbers))


def test_businesses_have_no_savings_accounts(
    reference_data: ReferenceData, customers_by_id: dict[str, Customer]
) -> None:
    for account in reference_data.accounts:
        if account.account_type is AccountType.SAVINGS:
            customer = customers_by_id[account.customer_id]
            assert customer.customer_type is CustomerType.INDIVIDUAL


def test_credit_card_balances_stay_within_the_limit(reference_data: ReferenceData) -> None:
    credit_accounts = [a for a in reference_data.accounts if a.credit_limit_cents is not None]
    assert credit_accounts
    for account in credit_accounts:
        assert account.credit_limit_cents is not None
        assert 0 <= account.opening_balance_cents <= account.credit_limit_cents * MAX_CREDIT_USED


def test_each_card_account_has_one_card_of_the_right_type(reference_data: ReferenceData) -> None:
    cards_by_account = {card.account_id: card for card in reference_data.cards}
    assert len(cards_by_account) == len(reference_data.cards)

    for account in reference_data.accounts:
        expected_type = CARD_TYPE_BY_ACCOUNT_TYPE.get(account.account_type)
        card = cards_by_account.get(account.account_id)
        if expected_type is None:
            assert card is None  # savings
        else:
            assert card is not None
            assert card.card_type is expected_type


def test_cards_are_issued_after_opening_and_valid_at_the_start(
    reference_data: ReferenceData,
) -> None:
    opened_at = {account.account_id: account.opened_at for account in reference_data.accounts}
    for card in reference_data.cards:
        assert opened_at[card.account_id] <= card.issued_at < START
        assert card.expires_on > START.date()
