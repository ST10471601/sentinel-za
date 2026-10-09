from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from sentinel.domain.accounts import Account, AccountStatus, AccountType, Card, CardStatus, CardType
from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.ledger import Ledger

OPENED = datetime(2024, 1, 10, tzinfo=UTC)
MORNING = datetime(2026, 3, 6, 8, 0, tzinfo=UTC)  # 10:00 SA time

CHEQUE = Account(
    account_id="ACC-0000001",
    customer_id="CUS-0000001",
    account_number="1234567890",
    account_type=AccountType.CHEQUE,
    opened_at=OPENED,
    status=AccountStatus.ACTIVE,
    opening_balance_cents=500_000,
    credit_limit_cents=None,
    daily_transfer_limit_cents=300_000,
    payshap_daily_limit_cents=150_000,
)
CREDIT = CHEQUE.model_copy(
    update={
        "account_id": "ACC-0000002",
        "account_number": "1234567891",
        "account_type": AccountType.CREDIT_CARD,
        "opening_balance_cents": 100_000,  # owed
        "credit_limit_cents": 400_000,
    }
)
DEBIT_CARD = Card(
    card_id="CRD-0000001",
    account_id=CHEQUE.account_id,
    card_type=CardType.DEBIT,
    pan_token="tok_0123456789abcdef01234567",
    pan_last4="4321",
    issued_at=OPENED,
    expires_on=date(2028, 1, 10),
    status=CardStatus.ACTIVE,
    daily_atm_limit_cents=200_000,
    daily_pos_limit_cents=250_000,
)
CREDIT_CARD = DEBIT_CARD.model_copy(
    update={"card_id": "CRD-0000002", "account_id": CREDIT.account_id, "card_type": CardType.CREDIT}
)

PURCHASE = TransactionDraft(
    account_id=CHEQUE.account_id,
    direction=Direction.DEBIT,
    amount_cents=100_000,
    event_time=MORNING,
    channel=Channel.CARD_PRESENT,
    auth_method=TransactionAuthMethod.PIN,
    country_code="ZA",
    card_id=DEBIT_CARD.card_id,
    merchant_id="MER-0000001",
    entry_mode=EntryMode.CHIP,
    terminal_lat=-26.1,
    terminal_lon=28.0,
)


@pytest.fixture
def ledger() -> Ledger:
    return Ledger([CHEQUE, CREDIT], [DEBIT_CARD, CREDIT_CARD])


def purchase(**overrides: Any) -> TransactionDraft:
    return replace(PURCHASE, **overrides)


def test_ledger_starts_from_opening_balances(ledger: Ledger) -> None:
    assert ledger.balance_cents(CHEQUE.account_id) == 500_000
    assert ledger.available_cents(CREDIT.account_id) == 300_000  # limit minus amount owed


def test_approved_debit_lowers_the_balance(ledger: Ledger) -> None:
    settlement = ledger.settle(purchase())
    assert settlement.status is TransactionStatus.APPROVED
    assert settlement.decline_reason is None
    assert settlement.balance_after_cents == 400_000
    assert ledger.balance_cents(CHEQUE.account_id) == 400_000


def test_credit_raises_the_balance_without_limit_checks(ledger: Ledger) -> None:
    salary = purchase(
        direction=Direction.CREDIT, channel=Channel.SALARY_CREDIT, amount_cents=9_000_000
    )
    assert ledger.settle(salary).balance_after_cents == 9_500_000


def test_debit_above_the_available_balance_is_declined_and_not_posted(ledger: Ledger) -> None:
    settlement = ledger.settle(purchase(channel=Channel.DEBIT_ORDER, amount_cents=600_000))
    assert settlement.status is TransactionStatus.DECLINED
    assert settlement.decline_reason is DeclineReason.INSUFFICIENT_FUNDS
    assert settlement.balance_after_cents == 500_000
    assert ledger.balance_cents(CHEQUE.account_id) == 500_000


def test_credit_card_purchase_raises_the_amount_owed(ledger: Ledger) -> None:
    card_purchase = purchase(account_id=CREDIT.account_id, card_id=CREDIT_CARD.card_id)
    assert ledger.settle(card_purchase).balance_after_cents == 200_000
    assert ledger.available_cents(CREDIT.account_id) == 200_000

    repayment = purchase(
        account_id=CREDIT.account_id, direction=Direction.CREDIT, channel=Channel.INTERNAL_TRANSFER
    )
    assert ledger.settle(repayment).balance_after_cents == 100_000


def test_credit_card_purchase_past_the_limit_is_declined(ledger: Ledger) -> None:
    too_big = purchase(
        account_id=CREDIT.account_id, card_id=CREDIT_CARD.card_id, amount_cents=300_001
    )
    assert ledger.settle(too_big).decline_reason is DeclineReason.INSUFFICIENT_FUNDS


@pytest.mark.parametrize(
    ("channel", "limit_cents"),
    [
        (Channel.CARD_PRESENT, 250_000),
        (Channel.ATM, 200_000),
        (Channel.PAYSHAP, 150_000),
        (Channel.EFT, 300_000),
    ],
)
def test_daily_limit_declines_once_the_day_total_would_pass_it(
    ledger: Ledger, channel: Channel, limit_cents: int
) -> None:
    first = purchase(channel=channel, amount_cents=limit_cents - 1_000)
    assert ledger.settle(first).status is TransactionStatus.APPROVED

    second = purchase(channel=channel, amount_cents=2_000)
    assert ledger.settle(second).decline_reason is DeclineReason.LIMIT_EXCEEDED


def test_declined_attempts_do_not_use_up_the_limit(ledger: Ledger) -> None:
    ledger.settle(purchase(channel=Channel.ATM, amount_cents=150_000))
    assert ledger.settle(purchase(channel=Channel.ATM, amount_cents=60_000)).decline_reason
    assert ledger.settle(purchase(channel=Channel.ATM, amount_cents=50_000)).decline_reason is None


def test_card_and_atm_limits_are_tracked_separately(ledger: Ledger) -> None:
    ledger.settle(purchase(channel=Channel.ATM, amount_cents=190_000))
    assert ledger.settle(purchase(amount_cents=200_000)).status is TransactionStatus.APPROVED


def test_daily_limit_resets_at_sa_midnight(ledger: Ledger) -> None:
    late_evening = datetime(2026, 3, 6, 21, 30, tzinfo=UTC)  # 23:30 SA time
    just_after_midnight = datetime(2026, 3, 6, 22, 30, tzinfo=UTC)  # 00:30 SA, next day

    ledger.settle(purchase(channel=Channel.ATM, amount_cents=190_000, event_time=late_evening))
    next_day = purchase(channel=Channel.ATM, amount_cents=190_000, event_time=just_after_midnight)
    assert ledger.settle(next_day).status is TransactionStatus.APPROVED


def test_debit_orders_have_no_daily_limit(ledger: Ledger) -> None:
    for _ in range(2):
        order = purchase(channel=Channel.DEBIT_ORDER, card_id=None, amount_cents=200_000)
        assert ledger.settle(order).status is TransactionStatus.APPROVED


def test_card_channel_draft_without_a_card_is_a_bug(ledger: Ledger) -> None:
    with pytest.raises(ValueError, match="no card_id"):
        ledger.settle(purchase(card_id=None))
