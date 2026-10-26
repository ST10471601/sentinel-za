import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.domain.accounts import Card
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.domain.transactions import Channel, EntryMode, TransactionAuthMethod
from sentinel.simulator.card_activity import BALANCE_CHECK_SECONDS
from sentinel.simulator.drafts import EnquiryDraft, TransactionDraft
from sentinel.simulator.fraud.cash_out import CASH_CENTS, Round, take_cash
from sentinel.simulator.fraud.episodes import FraudAction
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.spending import ATM_STEP_CENTS

START = datetime(2026, 1, 14, 8, 0, tzinfo=UTC)
SPREE = Round(START, START + timedelta(hours=12))
RICH = 100_000_000  # far more than any daily limit


@pytest.fixture(scope="module")
def card(reference_data: ReferenceData) -> Card:
    return reference_data.cards[0]


@pytest.fixture(scope="module")
def atms(reference_data: ReferenceData) -> list[Merchant]:
    return [m for m in reference_data.merchants if m.category is MerchantCategory.ATM][:6]


def withdrawals(attempts: Sequence[FraudAction]) -> list[TransactionDraft]:
    return [a for a in attempts if isinstance(a, TransactionDraft)]


def test_cash_is_taken_until_the_daily_limit_is_reached(card: Card, atms: list[Merchant]) -> None:
    for seed in range(50):
        attempts, ended_at = take_cash(random.Random(seed), card, atms, SPREE, RICH)
        cash = withdrawals(attempts)

        assert cash
        assert all(d.channel is Channel.ATM and d.card_id == card.card_id for d in cash)
        assert all(d.entry_mode is EntryMode.CHIP for d in cash)
        assert all(d.auth_method is TransactionAuthMethod.PIN for d in cash)
        assert all(CASH_CENTS[0] <= d.amount_cents <= CASH_CENTS[1] for d in cash)
        assert all(d.amount_cents % ATM_STEP_CENTS == 0 for d in cash)
        # Only the last withdrawal may go past the limit, and that one declines.
        assert sum(d.amount_cents for d in cash[:-1]) < card.daily_atm_limit_cents
        assert ended_at >= cash[-1].event_time


def test_an_empty_account_gets_one_withdrawal(card: Card, atms: list[Merchant]) -> None:
    for seed in range(20):
        attempts, _ = take_cash(random.Random(seed), card, atms, SPREE, 0)
        assert len(withdrawals(attempts)) == 1


def test_most_runs_start_with_a_balance_check_at_the_first_atm(
    card: Card, atms: list[Merchant]
) -> None:
    runs = [take_cash(random.Random(seed), card, atms, SPREE, RICH)[0] for seed in range(200)]
    checked = [run for run in runs if isinstance(run[0], EnquiryDraft)]
    low, high = (timedelta(seconds=s) for s in BALANCE_CHECK_SECONDS)

    assert 0.7 < len(checked) / len(runs) < 0.9
    for run in checked:
        enquiry, first = run[0], run[1]
        assert isinstance(enquiry, EnquiryDraft) and isinstance(first, TransactionDraft)
        assert enquiry.merchant_id == first.merchant_id
        assert low <= first.event_time - enquiry.event_time <= high
        assert not any(isinstance(a, EnquiryDraft) for a in run[1:])


def test_a_run_stops_at_the_end_of_its_round(card: Card, atms: list[Merchant]) -> None:
    short = Round(START, START + timedelta(seconds=1))

    attempts, ended_at = take_cash(random.Random(1), card, atms, short, RICH)

    assert [d.event_time for d in withdrawals(attempts)] == [START]
    assert ended_at >= short.end
