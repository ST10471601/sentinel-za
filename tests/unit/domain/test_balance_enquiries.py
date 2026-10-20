from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.transactions import DeclineReason, TransactionStatus


def balance_check(**overrides: object) -> dict[str, object]:
    """An approved balance check at an ATM."""
    fields: dict[str, object] = {
        "balance_enquiry_id": "BEQ-000000001",
        "card_id": "CRD-0000001",
        "account_id": "ACC-0000001",
        "merchant_id": "MER-0000001",
        "event_time": datetime(2026, 1, 25, 15, 12, tzinfo=UTC),
        "terminal_lat": -26.1076,
        "terminal_lon": 28.0567,
        "status": TransactionStatus.APPROVED,
        "decline_reason": None,
        "balance_cents": 120_000,
    }
    return fields | overrides


def test_valid_balance_check_round_trips() -> None:
    enquiry = BalanceEnquiry.model_validate(balance_check())
    assert BalanceEnquiry.model_validate(enquiry.model_dump()) == enquiry


def test_a_declined_check_on_a_blocked_card_is_valid() -> None:
    enquiry = BalanceEnquiry.model_validate(
        balance_check(status=TransactionStatus.DECLINED, decline_reason=DeclineReason.CARD_BLOCKED)
    )
    assert enquiry.decline_reason is DeclineReason.CARD_BLOCKED


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": TransactionStatus.DECLINED},
        {"decline_reason": DeclineReason.CARD_BLOCKED},
    ],
)
def test_decline_reason_goes_with_declined_checks_only(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="decline_reason"):
        BalanceEnquiry.model_validate(balance_check(**overrides))


def test_an_atm_has_a_location() -> None:
    with pytest.raises(ValidationError, match="terminal_lat"):
        BalanceEnquiry.model_validate(balance_check(terminal_lat=None))
