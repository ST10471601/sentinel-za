import random

import pytest

from sentinel.simulator.budget import OVERSPEND_CHANCE, DayBudget

ACCOUNT = "ACC-0000001"


def test_affordable_spends_go_ahead_and_reduce_what_is_left() -> None:
    budget = DayBudget(random.Random(1), {ACCOUNT: 10_000})
    assert budget.try_spend(ACCOUNT, 6_000)
    assert budget.remaining_cents(ACCOUNT) == 4_000


def test_unaffordable_spends_are_mostly_skipped_and_leave_the_budget_alone() -> None:
    budget = DayBudget(random.Random(1), {ACCOUNT: 1_000})
    attempts = sum(budget.try_spend(ACCOUNT, 5_000) for _ in range(5_000))
    assert attempts / 5_000 == pytest.approx(OVERSPEND_CHANCE, abs=0.02)
    assert budget.remaining_cents(ACCOUNT) == 1_000


def test_overdrawn_and_unknown_accounts_have_nothing_to_spend() -> None:
    budget = DayBudget(random.Random(1), {ACCOUNT: -5_000})
    assert budget.remaining_cents(ACCOUNT) == -5_000
    assert budget.remaining_cents("ACC-0000999") == 0
