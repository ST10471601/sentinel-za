"""Transactions: every entry posted to an account."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.accounts import AccountId, CardId
from sentinel.domain.base import CountryCode, DomainModel, Latitude, Longitude, PositiveCents
from sentinel.domain.beneficiaries import BeneficiaryId
from sentinel.domain.devices import DeviceId
from sentinel.domain.merchants import MerchantId
from sentinel.domain.sessions import SessionId

TransactionId = Annotated[str, Field(pattern=r"^TXN-\d{9}$")]
TransferGroupId = Annotated[str, Field(pattern=r"^TRF-\d{9}$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]  # ISO 4217

HOME_CURRENCY = "ZAR"


class Direction(StrEnum):
    """Money out of (debit) or into (credit) the account."""

    DEBIT = "debit"
    CREDIT = "credit"


class Channel(StrEnum):
    """How the money moved."""

    CARD_PRESENT = "card_present"
    CARD_NOT_PRESENT = "card_not_present"
    ATM = "atm"
    EFT = "eft"
    PAYSHAP = "payshap"
    INTERNAL_TRANSFER = "internal_transfer"
    SALARY_CREDIT = "salary_credit"
    DEBIT_ORDER = "debit_order"


class EntryMode(StrEnum):
    """How the card details were captured."""

    CHIP = "chip"
    CONTACTLESS = "contactless"
    MAGSTRIPE = "magstripe"
    ECOMMERCE = "ecommerce"
    MANUAL = "manual"


class TransactionAuthMethod(StrEnum):
    """How the payment was approved by the customer."""

    PIN = "pin"
    OTP = "otp"
    THREE_DS = "3ds"
    BIOMETRIC = "biometric"
    NONE = "none"


class TransactionStatus(StrEnum):
    """Whether the bank let the transaction through."""

    APPROVED = "approved"
    DECLINED = "declined"


class DeclineReason(StrEnum):
    """Why a transaction was declined."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    LIMIT_EXCEEDED = "limit_exceeded"


CARD_CHANNELS = frozenset({Channel.CARD_PRESENT, Channel.CARD_NOT_PRESENT, Channel.ATM})
TERMINAL_CHANNELS = frozenset({Channel.CARD_PRESENT, Channel.ATM})
PAYMENT_CHANNELS = frozenset({Channel.EFT, Channel.PAYSHAP, Channel.INTERNAL_TRANSFER})
DEBIT_ONLY_CHANNELS = frozenset({*CARD_CHANNELS, Channel.DEBIT_ORDER})
CREDIT_ONLY_CHANNELS = frozenset({Channel.SALARY_CREDIT})


class Transaction(DomainModel):
    """One entry posted to an account.

    An internal transfer is two entries, a debit and a credit, sharing a transfer_group_id.
    """

    transaction_id: TransactionId
    transfer_group_id: TransferGroupId | None
    account_id: AccountId
    direction: Direction
    amount_cents: PositiveCents
    original_amount_cents: PositiveCents | None
    original_currency: CurrencyCode | None
    event_time: UtcDatetime
    channel: Channel
    card_id: CardId | None
    merchant_id: MerchantId | None
    beneficiary_id: BeneficiaryId | None
    counterparty_account_id: AccountId | None
    counterparty_external_ref: str | None = Field(min_length=1)
    session_id: SessionId | None
    device_id: DeviceId | None
    entry_mode: EntryMode | None
    auth_method: TransactionAuthMethod
    terminal_lat: Latitude | None
    terminal_lon: Longitude | None
    country_code: CountryCode
    status: TransactionStatus
    decline_reason: DeclineReason | None
    balance_after_cents: int  # can be negative on an overdraft
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_direction(self) -> Self:
        """Card, ATM and debit-order entries are debits; salaries are credits."""
        if self.channel in DEBIT_ONLY_CHANNELS and self.direction is Direction.CREDIT:
            raise ValueError(f"{self.channel} only allows the debit direction")
        if self.channel in CREDIT_ONLY_CHANNELS and self.direction is Direction.DEBIT:
            raise ValueError(f"{self.channel} only allows the credit direction")
        return self

    @model_validator(mode="after")
    def check_card_fields(self) -> Self:
        """Card channels need card, merchant and entry mode; other channels have none."""
        card_fields = (self.card_id, self.merchant_id, self.entry_mode)
        if self.channel in CARD_CHANNELS and any(field is None for field in card_fields):
            raise ValueError("card channels need card_id, merchant_id and entry_mode")
        if self.channel not in CARD_CHANNELS and any(field is not None for field in card_fields):
            raise ValueError("card_id, merchant_id and entry_mode are only for card channels")
        return self

    @model_validator(mode="after")
    def check_terminal_location(self) -> Self:
        """In-person card and ATM entries have a terminal location; nothing else does."""
        has_location = self.terminal_lat is not None and self.terminal_lon is not None
        has_any = self.terminal_lat is not None or self.terminal_lon is not None
        if self.channel in TERMINAL_CHANNELS and not has_location:
            raise ValueError(f"{self.channel} needs terminal_lat and terminal_lon")
        if self.channel not in TERMINAL_CHANNELS and has_any:
            raise ValueError(f"{self.channel} has no terminal location")
        return self

    @model_validator(mode="after")
    def check_payment_fields(self) -> Self:
        """Outgoing payments come from a login session; the device needs that session."""
        if self.channel not in PAYMENT_CHANNELS and (self.session_id or self.beneficiary_id):
            raise ValueError("session_id and beneficiary_id are for eft, payshap or transfers")
        if self.device_id is not None and self.session_id is None:
            raise ValueError("device_id needs a session_id")
        is_outgoing_payment = self.channel in PAYMENT_CHANNELS and self.direction is Direction.DEBIT
        if is_outgoing_payment and self.session_id is None:
            raise ValueError("outgoing payments need a session_id")
        return self

    @model_validator(mode="after")
    def check_internal_transfer(self) -> Self:
        """Both legs of an internal transfer share a group and name the other account."""
        is_transfer = self.channel is Channel.INTERNAL_TRANSFER
        if is_transfer and (self.transfer_group_id is None or self.counterparty_account_id is None):
            raise ValueError("internal_transfer needs transfer_group_id and counterparty")
        if not is_transfer and self.transfer_group_id is not None:
            raise ValueError("transfer_group_id is only for internal_transfer")
        return self

    @model_validator(mode="after")
    def check_foreign_amount(self) -> Self:
        """A foreign entry keeps its original amount and a non-rand currency."""
        if (self.original_amount_cents is None) != (self.original_currency is None):
            raise ValueError("original_amount_cents and original_currency go together")
        if self.original_currency == HOME_CURRENCY:
            raise ValueError("original_currency is only for foreign currencies")
        return self

    @model_validator(mode="after")
    def check_decline_reason(self) -> Self:
        """Declined entries, and only those, have a decline_reason."""
        is_declined = self.status is TransactionStatus.DECLINED
        if is_declined != (self.decline_reason is not None):
            raise ValueError("decline_reason is required for declined transactions only")
        return self
