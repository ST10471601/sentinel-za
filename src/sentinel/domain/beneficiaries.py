"""Saved payees and the history of changes to them."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.accounts import AccountId, AccountNumber
from sentinel.domain.base import DomainModel
from sentinel.domain.customers import CustomerId, SaMobileNumber
from sentinel.domain.sessions import SessionId

BeneficiaryId = Annotated[str, Field(pattern=r"^BEN-\d{7}$")]
BeneficiaryEventId = Annotated[str, Field(pattern=r"^BEV-\d{7}$")]


class BeneficiaryEventType(StrEnum):
    """What happened to a saved payee."""

    CREATED = "created"
    DETAILS_CHANGED = "details_changed"
    DELETED = "deleted"


class Beneficiary(DomainModel):
    """A payee a customer saved, with its current details."""

    beneficiary_id: BeneficiaryId
    customer_id: CustomerId
    beneficiary_name: str = Field(min_length=1)
    payee_bank: str = Field(min_length=1)
    payee_account_number: AccountNumber
    payee_internal_account_id: AccountId | None  # set when the payee banks with us
    shap_id: SaMobileNumber | None  # PayShap phone proxy
    created_at: UtcDatetime


class BeneficiaryEvent(DomainModel):
    """One change to a saved payee.

    A details change followed by a large payment is the shape of supplier-mandate fraud.
    """

    beneficiary_event_id: BeneficiaryEventId
    beneficiary_id: BeneficiaryId
    event_type: BeneficiaryEventType
    event_time: UtcDatetime
    session_id: SessionId
    old_account_number: AccountNumber | None
    new_account_number: AccountNumber | None

    @model_validator(mode="after")
    def check_account_numbers(self) -> Self:
        """Each event type carries the old and new account numbers it changes."""
        has_old = self.old_account_number is not None
        has_new = self.new_account_number is not None
        match self.event_type:
            case BeneficiaryEventType.CREATED if has_old or not has_new:
                raise ValueError("created events need new_account_number only")
            case BeneficiaryEventType.DELETED if has_new or not has_old:
                raise ValueError("deleted events need old_account_number only")
            case BeneficiaryEventType.DETAILS_CHANGED if (
                not (has_old and has_new) or self.old_account_number == self.new_account_number
            ):
                raise ValueError("details_changed events need two different account numbers")
        return self
