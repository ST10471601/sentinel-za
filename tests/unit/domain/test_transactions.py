from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    Transaction,
    TransactionAuthMethod,
    TransactionStatus,
)


def card_purchase(**overrides: object) -> dict[str, object]:
    """A contactless grocery purchase: the base shape for most tests."""
    fields: dict[str, object] = {
        "transaction_id": "TXN-000000001",
        "transfer_group_id": None,
        "account_id": "ACC-0000001",
        "direction": Direction.DEBIT,
        "amount_cents": 45_990,
        "original_amount_cents": None,
        "original_currency": None,
        "event_time": datetime(2026, 1, 25, 15, 12, tzinfo=UTC),
        "channel": Channel.CARD_PRESENT,
        "card_id": "CRD-0000001",
        "merchant_id": "MER-0000001",
        "beneficiary_id": None,
        "counterparty_account_id": None,
        "counterparty_external_ref": None,
        "session_id": None,
        "device_id": None,
        "entry_mode": EntryMode.CONTACTLESS,
        "auth_method": TransactionAuthMethod.NONE,
        "terminal_lat": -26.1076,
        "terminal_lon": 28.0567,
        "country_code": "ZA",
        "status": TransactionStatus.APPROVED,
        "decline_reason": None,
        "balance_after_cents": 1_204_010,
    }
    return fields | overrides


def payment(**overrides: object) -> dict[str, object]:
    """An app payment to a saved beneficiary."""
    fields: dict[str, object] = {
        "channel": Channel.EFT,
        "card_id": None,
        "merchant_id": None,
        "entry_mode": None,
        "terminal_lat": None,
        "terminal_lon": None,
        "beneficiary_id": "BEN-0000001",
        "session_id": "SES-0000001",
        "device_id": "DEV-0000001",
        "auth_method": TransactionAuthMethod.OTP,
    }
    return card_purchase(**(fields | overrides))


def internal_transfer(**overrides: object) -> dict[str, object]:
    """One leg of a transfer to another account at our bank."""
    fields: dict[str, object] = {
        "channel": Channel.INTERNAL_TRANSFER,
        "transfer_group_id": "TRF-000000001",
        "beneficiary_id": None,
        "counterparty_account_id": "ACC-0000002",
    }
    return payment(**(fields | overrides))


def salary_credit(**overrides: object) -> dict[str, object]:
    """A salary paid in by an employer at another bank."""
    fields: dict[str, object] = {
        "channel": Channel.SALARY_CREDIT,
        "direction": Direction.CREDIT,
        "beneficiary_id": None,
        "session_id": None,
        "device_id": None,
        "counterparty_external_ref": "EMP-ACME-PAYROLL",
        "auth_method": TransactionAuthMethod.NONE,
    }
    return payment(**(fields | overrides))


@pytest.mark.parametrize(
    "fields", [card_purchase(), payment(), internal_transfer(), salary_credit()]
)
def test_valid_transactions_are_accepted(fields: dict[str, object]) -> None:
    assert Transaction.model_validate(fields).schema_version == 1


def test_online_purchase_has_no_terminal_location() -> None:
    online = card_purchase(
        channel=Channel.CARD_NOT_PRESENT,
        entry_mode=EntryMode.ECOMMERCE,
        auth_method=TransactionAuthMethod.THREE_DS,
        terminal_lat=None,
        terminal_lon=None,
    )
    assert Transaction.model_validate(online).terminal_lat is None

    with pytest.raises(ValidationError, match="terminal"):
        Transaction.model_validate(online | {"terminal_lat": -26.1, "terminal_lon": 28.0})


@pytest.mark.parametrize("channel", [Channel.CARD_PRESENT, Channel.ATM])
def test_card_present_and_atm_need_a_terminal_location(channel: Channel) -> None:
    with pytest.raises(ValidationError, match="terminal"):
        Transaction.model_validate(card_purchase(channel=channel, terminal_lon=None))


@pytest.mark.parametrize("missing", ["card_id", "merchant_id", "entry_mode"])
def test_card_channels_need_card_merchant_and_entry_mode(missing: str) -> None:
    with pytest.raises(ValidationError, match="card"):
        Transaction.model_validate(card_purchase(**{missing: None}))


def test_non_card_channels_have_no_card_fields() -> None:
    with pytest.raises(ValidationError, match="card"):
        Transaction.model_validate(payment(card_id="CRD-0000001"))


@pytest.mark.parametrize(
    ("channel", "direction"),
    [
        (Channel.CARD_PRESENT, Direction.CREDIT),
        (Channel.ATM, Direction.CREDIT),
        (Channel.DEBIT_ORDER, Direction.CREDIT),
        (Channel.SALARY_CREDIT, Direction.DEBIT),
    ],
)
def test_one_way_channels_reject_the_wrong_direction(
    channel: Channel, direction: Direction
) -> None:
    uses_card = channel in {Channel.CARD_PRESENT, Channel.ATM}
    fields = card_purchase() if uses_card else salary_credit()
    with pytest.raises(ValidationError, match="direction"):
        Transaction.model_validate(fields | {"channel": channel, "direction": direction})


def test_outgoing_payments_need_a_session() -> None:
    with pytest.raises(ValidationError, match="session"):
        Transaction.model_validate(payment(session_id=None, device_id=None))


def test_ussd_payment_has_a_session_but_no_device() -> None:
    ussd = payment(channel=Channel.PAYSHAP, device_id=None, auth_method=TransactionAuthMethod.PIN)
    assert Transaction.model_validate(ussd).device_id is None


def test_device_needs_a_session() -> None:
    with pytest.raises(ValidationError, match="device_id"):
        Transaction.model_validate(salary_credit(device_id="DEV-0000001"))


@pytest.mark.parametrize(
    "fields",
    [card_purchase(session_id="SES-0000001"), card_purchase(beneficiary_id="BEN-0000001")],
)
def test_card_transactions_have_no_session_or_beneficiary(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="eft, payshap"):
        Transaction.model_validate(fields)


def test_incoming_eft_from_another_bank_has_no_session() -> None:
    incoming = payment(
        direction=Direction.CREDIT,
        beneficiary_id=None,
        session_id=None,
        device_id=None,
        counterparty_external_ref="EXT-REF-123",
        auth_method=TransactionAuthMethod.NONE,
    )
    assert Transaction.model_validate(incoming).session_id is None


@pytest.mark.parametrize("missing", ["transfer_group_id", "counterparty_account_id"])
def test_internal_transfer_needs_group_and_counterparty(missing: str) -> None:
    with pytest.raises(ValidationError, match="internal_transfer"):
        Transaction.model_validate(internal_transfer(**{missing: None}))


def test_transfer_group_is_only_for_internal_transfers() -> None:
    with pytest.raises(ValidationError, match="transfer_group_id"):
        Transaction.model_validate(payment(transfer_group_id="TRF-000000001"))


def test_foreign_purchase_keeps_the_original_amount_and_currency() -> None:
    foreign = card_purchase(original_amount_cents=2_500, original_currency="USD")
    assert Transaction.model_validate(foreign).original_currency == "USD"


@pytest.mark.parametrize(
    "overrides",
    [
        {"original_amount_cents": 2_500},
        {"original_currency": "USD"},
        {"original_amount_cents": 2_500, "original_currency": "ZAR"},
    ],
)
def test_original_amount_needs_a_foreign_currency(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="original"):
        Transaction.model_validate(card_purchase(**overrides))


def test_declined_transaction_needs_a_reason() -> None:
    declined = card_purchase(
        status=TransactionStatus.DECLINED, decline_reason=DeclineReason.INSUFFICIENT_FUNDS
    )
    assert Transaction.model_validate(declined).decline_reason is DeclineReason.INSUFFICIENT_FUNDS

    with pytest.raises(ValidationError, match="decline_reason"):
        Transaction.model_validate(declined | {"decline_reason": None})
    with pytest.raises(ValidationError, match="decline_reason"):
        Transaction.model_validate(card_purchase(decline_reason=DeclineReason.LIMIT_EXCEEDED))


def test_balance_can_go_below_zero() -> None:
    overdrawn = Transaction.model_validate(card_purchase(balance_after_cents=-12_000))
    assert overdrawn.balance_after_cents == -12_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"transaction_id": "TXN-0000001"},
        {"amount_cents": 0},
        {"original_currency": "usd", "original_amount_cents": 2_500},
        {"country_code": "ZAF"},
        {"schema_version": 0},
        {"event_time": datetime(2026, 1, 25, 15, 12)},  # noqa: DTZ001 - deliberately naive
    ],
)
def test_malformed_transaction_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Transaction.model_validate(card_purchase(**overrides))
