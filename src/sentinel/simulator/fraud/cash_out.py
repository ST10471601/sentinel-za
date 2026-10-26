"""Cash-outs: a fraudster with a card and its PIN empties the account at a few ATMs."""

import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sentinel.domain.accounts import Card
from sentinel.domain.merchants import Merchant
from sentinel.simulator.card_activity import (
    BALANCE_CHECK_SECONDS,
    atm_withdrawal_draft,
    balance_enquiry_draft,
)
from sentinel.simulator.drafts import EnquiryDraft
from sentinel.simulator.fraud.episodes import FraudAction
from sentinel.simulator.randomness import chance
from sentinel.simulator.spending import ATM_STEP_CENTS

# Simulation assumptions.
BALANCE_CHECK_SHARE = 0.8  # checks what there is to take before the first withdrawal
ATMS_PER_ROUND = (2, 4)
WITHDRAWALS_PER_ATM = (1, 3)
CASH_CENTS = (100_000, 300_000)  # R1,000 to R3,000 per withdrawal
ATM_GAP_SECONDS = (30, 180)  # between withdrawals at one ATM
DRIVE_SECONDS = (300, 1_500)  # between ATMs and shops


@dataclass(frozen=True, slots=True)
class Round:
    """A spree between two times. How much it takes depends on the balance when it starts."""

    start: datetime
    end: datetime


def take_cash(
    rng: random.Random, card: Card, atms: Sequence[Merchant], spree: Round, available_cents: int
) -> tuple[list[FraudAction], datetime]:
    """Withdraw until the money or the daily ATM limit runs out, and say when the run ends."""
    target = min(available_cents, card.daily_atm_limit_cents)
    attempts: list[FraudAction] = []
    taken = 0
    moment = spree.start
    for number, (atm, moment) in enumerate(_atm_visits(rng, atms, spree.start)):
        is_first = number == 0
        # The withdrawal that goes past the target is the one that declines.
        if moment >= spree.end or (not is_first and taken >= target):
            break
        if is_first and chance(rng, BALANCE_CHECK_SHARE):
            attempts.append(_balance_check(rng, card, atm, moment))
        amount = cash_cents(rng)
        attempts.append(atm_withdrawal_draft(card, atm, amount, moment))
        taken += amount
    return attempts, moment


def cash_cents(rng: random.Random) -> int:
    """One withdrawal, in whole notes."""
    return rng.randint(*CASH_CENTS) // ATM_STEP_CENTS * ATM_STEP_CENTS


def _atm_visits(
    rng: random.Random, atms: Sequence[Merchant], start: datetime
) -> Iterator[tuple[Merchant, datetime]]:
    """A few ATMs, one after another, with one to three withdrawals at each."""
    moment = start
    count = min(rng.randint(*ATMS_PER_ROUND), len(atms))
    for atm in rng.sample(atms, count):
        for _ in range(rng.randint(*WITHDRAWALS_PER_ATM)):
            yield atm, moment
            moment += _seconds(rng, ATM_GAP_SECONDS)
        moment += _seconds(rng, DRIVE_SECONDS)


def _balance_check(
    rng: random.Random, card: Card, atm: Merchant, withdrawn_at: datetime
) -> EnquiryDraft:
    """A balance check at the ATM, a moment before the withdrawal."""
    return balance_enquiry_draft(card, atm, withdrawn_at - _seconds(rng, BALANCE_CHECK_SECONDS))


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))
