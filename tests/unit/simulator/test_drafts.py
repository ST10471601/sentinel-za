from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    Transaction,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.drafts import Settlement, TransactionDraft, to_transaction

APPROVED = Settlement(TransactionStatus.APPROVED, None, 950_000)


SALARY = TransactionDraft(
    account_id="ACC-0000001",
    direction=Direction.CREDIT,
    amount_cents=2_500_000,
    event_time=datetime(2026, 1, 23, 2, 15, tzinfo=UTC),
    channel=Channel.SALARY_CREDIT,
    auth_method=TransactionAuthMethod.NONE,
    country_code="ZA",
    counterparty_external_ref="EMP-0000001",
)


def salary_draft(**overrides: Any) -> TransactionDraft:
    return replace(SALARY, **overrides)


def test_draft_and_settlement_cover_every_transaction_field() -> None:
    draft_fields = {field.name for field in fields(TransactionDraft)}
    settlement_fields = {field.name for field in fields(Settlement)}
    added_on_conversion = {"transaction_id", "schema_version"}

    assert not draft_fields & settlement_fields
    assert draft_fields | settlement_fields | added_on_conversion == set(Transaction.model_fields)


def test_draft_becomes_a_transaction_with_a_nine_digit_id() -> None:
    transaction = to_transaction(salary_draft(), 42, APPROVED)
    assert transaction.transaction_id == "TXN-000000042"
    assert transaction.balance_after_cents == 950_000
    assert transaction.status is TransactionStatus.APPROVED


def test_declined_settlement_carries_its_reason() -> None:
    declined = Settlement(TransactionStatus.DECLINED, DeclineReason.INSUFFICIENT_FUNDS, 10_000)
    draft = salary_draft(channel=Channel.DEBIT_ORDER, direction=Direction.DEBIT)
    transaction = to_transaction(draft, 1, declined)
    assert transaction.decline_reason is DeclineReason.INSUFFICIENT_FUNDS


def test_invalid_draft_is_rejected_on_conversion() -> None:
    with pytest.raises(ValidationError, match="direction"):
        to_transaction(salary_draft(direction=Direction.DEBIT), 1, APPROVED)
