"""SIM swaps reported to the bank by mobile networks."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.base import DomainModel
from sentinel.domain.customers import CustomerId, SaMobileNumber

SimSwapId = Annotated[str, Field(pattern=r"^SIM-\d{7}$")]
MobileNetwork = Annotated[str, Field(pattern=r"^MNO-[1-4]$")]  # fictional networks


class SimSwapEvent(DomainModel):
    """A customer's number moved to a new SIM card.

    Most swaps are legitimate (lost or upgraded phones). The bank only hears about a
    swap at notified_at, which can be hours after it happened.
    """

    sim_swap_id: SimSwapId
    customer_id: CustomerId
    phone_number: SaMobileNumber
    swapped_at: UtcDatetime
    notified_at: UtcDatetime
    network: MobileNetwork

    @model_validator(mode="after")
    def check_notified_after_swap(self) -> Self:
        """The bank can't be told about a swap before it happens."""
        if self.notified_at < self.swapped_at:
            raise ValueError("notified_at cannot be before swapped_at")
        return self
