from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sentinel.domain.sim_swaps import SimSwapEvent

SWAPPED_AT = datetime(2026, 2, 3, 9, 15, tzinfo=UTC)


def sim_swap_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "sim_swap_id": "SIM-0000001",
        "customer_id": "CUS-0000001",
        "phone_number": "+27821234567",
        "swapped_at": SWAPPED_AT,
        "notified_at": SWAPPED_AT + timedelta(hours=3),
        "network": "MNO-2",
    }
    return fields | overrides


def test_valid_sim_swap_is_accepted() -> None:
    swap = SimSwapEvent.model_validate(sim_swap_fields())
    assert swap.network == "MNO-2"


def test_bank_can_be_notified_at_the_moment_of_the_swap() -> None:
    swap = SimSwapEvent.model_validate(sim_swap_fields(notified_at=SWAPPED_AT))
    assert swap.notified_at == swap.swapped_at


def test_notification_cannot_come_before_the_swap() -> None:
    with pytest.raises(ValidationError, match="notified_at"):
        SimSwapEvent.model_validate(sim_swap_fields(notified_at=SWAPPED_AT - timedelta(minutes=1)))


@pytest.mark.parametrize("network", ["MNO-0", "MNO-5", "Vodacom"])
def test_only_fictional_networks_are_allowed(network: str) -> None:
    with pytest.raises(ValidationError):
        SimSwapEvent.model_validate(sim_swap_fields(network=network))
