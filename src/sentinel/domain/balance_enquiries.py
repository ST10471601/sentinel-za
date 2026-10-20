"""Balance enquiries: a cardholder checking the balance at an ATM. No money moves."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.accounts import AccountId, CardId
from sentinel.domain.base import DomainModel, Latitude, Longitude
from sentinel.domain.merchants import MerchantId
from sentinel.domain.transactions import DeclineReason, TransactionStatus

BalanceEnquiryId = Annotated[str, Field(pattern=r"^BEQ-\d{9}$")]


class BalanceEnquiry(DomainModel):
    """One balance check at an ATM, as the card switch logs it. It is never posted."""

    balance_enquiry_id: BalanceEnquiryId
    card_id: CardId
    account_id: AccountId
    merchant_id: MerchantId  # the ATM
    event_time: UtcDatetime
    terminal_lat: Latitude
    terminal_lon: Longitude
    status: TransactionStatus
    decline_reason: DeclineReason | None
    balance_cents: int  # the account balance at the time, shown on screen when approved

    @model_validator(mode="after")
    def check_decline_reason(self) -> Self:
        """Declined enquiries, and only those, have a decline_reason."""
        is_declined = self.status is TransactionStatus.DECLINED
        if is_declined != (self.decline_reason is not None):
            raise ValueError("decline_reason is required for declined enquiries only")
        return self
