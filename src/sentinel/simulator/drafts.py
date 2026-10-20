"""Drafts: what a customer tries to do, before the bank settles it."""

from dataclasses import asdict, dataclass
from datetime import datetime

from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    Transaction,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.identifiers import make_id

TRANSACTION_ID_DIGITS = 9


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionDraft:
    """Every transaction field the customer side decides.

    The bank adds the rest when it settles the draft: the ID, the status and the balance.
    Fields are checked when the draft becomes a Transaction, not here.
    """

    account_id: str
    direction: Direction
    amount_cents: int
    event_time: datetime
    channel: Channel
    auth_method: TransactionAuthMethod
    country_code: str
    transfer_group_id: str | None = None
    original_amount_cents: int | None = None
    original_currency: str | None = None
    card_id: str | None = None
    merchant_id: str | None = None
    beneficiary_id: str | None = None
    counterparty_account_id: str | None = None
    counterparty_external_ref: str | None = None
    session_id: str | None = None
    device_id: str | None = None
    entry_mode: EntryMode | None = None
    terminal_lat: float | None = None
    terminal_lon: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EnquiryDraft:
    """A balance check at an ATM, before the bank answers it. Checked when it becomes a model."""

    card_id: str
    account_id: str
    merchant_id: str
    event_time: datetime
    terminal_lat: float | None
    terminal_lon: float | None


@dataclass(frozen=True, slots=True)
class Settlement:
    """The bank's answer to a draft."""

    status: TransactionStatus
    decline_reason: DeclineReason | None
    balance_after_cents: int


def to_transaction(draft: TransactionDraft, number: int, settlement: Settlement) -> Transaction:
    """Combine a draft and its settlement into a validated Transaction."""
    return Transaction.model_validate(
        {
            "transaction_id": make_id("TXN", number, digits=TRANSACTION_ID_DIGITS),
            **asdict(draft),
            **asdict(settlement),
        }
    )


def to_balance_enquiry(draft: EnquiryDraft, number: int, settlement: Settlement) -> BalanceEnquiry:
    """Combine an enquiry draft and the bank's answer into a validated BalanceEnquiry."""
    return BalanceEnquiry.model_validate(
        {
            "balance_enquiry_id": make_id("BEQ", number, digits=TRANSACTION_ID_DIGITS),
            **asdict(draft),
            "status": settlement.status,
            "decline_reason": settlement.decline_reason,
            "balance_cents": settlement.balance_after_cents,
        }
    )
