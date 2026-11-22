"""Moving a victim's money into the main account and splitting it into payouts.

Shared by the scenarios that empty an account through online banking: SIM swap takeovers and
authorised push payment scams.
"""

import itertools
import random
from collections.abc import Callable
from datetime import timedelta

from sentinel.domain.sessions import LoginSession
from sentinel.simulator.fraud.episodes import Balances
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import (
    PAYMENT_DELAY_SECONDS,
    PaymentHabits,
    PlannedPayment,
    own_transfer,
)

RAND_CENTS = 100  # payouts are typed in whole rands


def sweep_into_main(
    rng: random.Random,
    transfer_groups: IdSequence,
    habits: PaymentHabits,
    session: LoginSession,
    balances: Balances,
    amount_to_move: Callable[[int], int],
) -> tuple[list[PlannedPayment], int]:
    """Move money from savings, then the credit card, into the main account.

    ``amount_to_move`` turns what an account can move today into the amount moved. Returns the
    transfers and how much the main account will then hold.
    """
    main = habits.main_account
    budget = balances.available_cents(main.account_id)
    moment = session.started_at
    transfers: list[PlannedPayment] = []
    for source in (habits.savings_account, habits.credit_account):
        if source is None:
            continue
        available = balances.available_cents(source.account_id)
        amount = amount_to_move(min(available, source.daily_transfer_limit_cents))
        if amount <= 0:
            continue
        moment += timedelta(seconds=rng.randint(*PAYMENT_DELAY_SECONDS))
        transfers.append(own_transfer(transfer_groups, session, source, main, amount, moment))
        budget += amount
    return transfers, budget


def split(rng: random.Random, total_cents: int, parts: int) -> list[int]:
    """Random whole-rand shares of ``total_cents``, at most ``parts`` of them, none empty."""
    rands = total_cents // RAND_CENTS
    parts = min(parts, rands)
    if parts < 1:
        return []
    cuts = sorted(rng.sample(range(1, rands), parts - 1))
    return [(end - start) * RAND_CENTS for start, end in itertools.pairwise([0, *cuts, rands])]


def whole_rands(cents: int) -> int:
    """``cents`` rounded down to whole rands."""
    return cents // RAND_CENTS * RAND_CENTS
