"""Bank accounts and the cards linked to them."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.base import DomainModel, PositiveCents
from sentinel.domain.customers import CustomerId

AccountId = Annotated[str, Field(pattern=r"^ACC-\d{7}$")]
CardId = Annotated[str, Field(pattern=r"^CRD-\d{7}$")]

AccountNumber = Annotated[str, Field(pattern=r"^\d{10,11}$")]
CardToken = Annotated[str, Field(pattern=r"^tok_[0-9a-f]{24}$")]
PanLast4 = Annotated[str, Field(pattern=r"^\d{4}$")]


class AccountType(StrEnum):
    """Kind of account."""

    CHEQUE = "cheque"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    BUSINESS_CURRENT = "business_current"


class AccountStatus(StrEnum):
    """Whether the account can be used."""

    ACTIVE = "active"
    DORMANT = "dormant"
    FROZEN = "frozen"
    CLOSED = "closed"


class CardType(StrEnum):
    """Debit cards draw on the account balance; credit cards on a credit limit."""

    DEBIT = "debit"
    CREDIT = "credit"


class CardStatus(StrEnum):
    """Whether the card can be used."""

    ACTIVE = "active"
    BLOCKED = "blocked"
    REPORTED_LOST = "reported_lost"


class Account(DomainModel):
    """One account owned by a customer.

    For credit-card accounts the balance is the amount owed. Cheque accounts may go
    below zero (overdraft), so the opening balance is not limited to positive values.
    """

    account_id: AccountId
    customer_id: CustomerId
    account_number: AccountNumber
    account_type: AccountType
    opened_at: UtcDatetime
    status: AccountStatus
    opening_balance_cents: int
    credit_limit_cents: PositiveCents | None
    daily_transfer_limit_cents: PositiveCents
    payshap_daily_limit_cents: PositiveCents

    @model_validator(mode="after")
    def check_credit_limit(self) -> Self:
        """Credit-card accounts, and only those, have a credit limit."""
        is_credit_card = self.account_type is AccountType.CREDIT_CARD
        if is_credit_card != (self.credit_limit_cents is not None):
            raise ValueError("credit_limit_cents is required for credit-card accounts only")
        return self


class Card(DomainModel):
    """A card linked to an account.

    Full card numbers are never stored (PCI DSS practice): only a token and the last 4 digits.
    """

    card_id: CardId
    account_id: AccountId
    card_type: CardType
    pan_token: CardToken
    pan_last4: PanLast4
    issued_at: UtcDatetime
    expires_on: date
    status: CardStatus
    daily_atm_limit_cents: PositiveCents
    daily_pos_limit_cents: PositiveCents

    @model_validator(mode="after")
    def check_expiry(self) -> Self:
        """A card must expire after the day it was issued."""
        if self.expires_on <= self.issued_at.date():
            raise ValueError("expires_on must be after issued_at")
        return self
