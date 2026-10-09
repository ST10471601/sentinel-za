from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.accounts import Account, AccountStatus, AccountType, Card, CardStatus, CardType


def account_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "account_id": "ACC-0000001",
        "customer_id": "CUS-0000001",
        "account_number": "1234567890",
        "account_type": AccountType.CHEQUE,
        "opened_at": datetime(2021, 6, 1, 9, 0, tzinfo=UTC),
        "status": AccountStatus.ACTIVE,
        "opening_balance_cents": 1_250_000,
        "credit_limit_cents": None,
        "daily_transfer_limit_cents": 5_000_000,
        "payshap_daily_limit_cents": 5_000_000,
    }
    return fields | overrides


def card_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "card_id": "CRD-0000001",
        "account_id": "ACC-0000001",
        "card_type": CardType.DEBIT,
        "pan_token": "tok_0123456789abcdef01234567",
        "pan_last4": "4321",
        "issued_at": datetime(2024, 2, 1, 10, 0, tzinfo=UTC),
        "expires_on": date(2028, 2, 29),
        "status": CardStatus.ACTIVE,
        "daily_atm_limit_cents": 500_000,
        "daily_pos_limit_cents": 2_000_000,
    }
    return fields | overrides


def test_valid_account_is_accepted() -> None:
    assert Account.model_validate(account_fields()).account_type is AccountType.CHEQUE


def test_overdrawn_opening_balance_is_allowed() -> None:
    account = Account.model_validate(account_fields(opening_balance_cents=-50_000))
    assert account.opening_balance_cents == -50_000


def test_credit_card_account_needs_a_credit_limit() -> None:
    with pytest.raises(ValidationError, match="credit-card accounts only"):
        Account.model_validate(account_fields(account_type=AccountType.CREDIT_CARD))

    credit_account = account_fields(
        account_type=AccountType.CREDIT_CARD, credit_limit_cents=3_000_000
    )
    assert Account.model_validate(credit_account).credit_limit_cents == 3_000_000


def test_other_accounts_have_no_credit_limit() -> None:
    with pytest.raises(ValidationError, match="credit-card accounts only"):
        Account.model_validate(account_fields(credit_limit_cents=3_000_000))


@pytest.mark.parametrize(
    "overrides",
    [
        {"account_number": "12345"},
        {"daily_transfer_limit_cents": 0},
        {"customer_id": "CUST-0000001"},
        {"opened_at": datetime(2021, 6, 1, 9, 0)},  # noqa: DTZ001 - deliberately naive
    ],
)
def test_malformed_account_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Account.model_validate(account_fields(**overrides))


def test_valid_card_is_accepted() -> None:
    assert Card.model_validate(card_fields()).pan_last4 == "4321"


def test_card_must_expire_after_issue_date() -> None:
    with pytest.raises(ValidationError, match="expires_on must be after issued_at"):
        Card.model_validate(card_fields(expires_on=date(2024, 2, 1)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"pan_last4": "432"},
        {"pan_token": "4111111111111111"},  # a full card number is never accepted
        {"daily_atm_limit_cents": -1},
    ],
)
def test_malformed_card_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Card.model_validate(card_fields(**overrides))
