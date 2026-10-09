"""How much a customer feels they can spend today, so spending follows the balance."""

import random
from collections.abc import Mapping

from sentinel.simulator.randomness import chance

# Simulation assumption: share of unaffordable spends a customer tries anyway.
# The bank then declines them, which gives the data realistic declines.
OVERSPEND_CHANCE = 0.1


class DayBudget:
    """Money left to spend today, per account.

    Starts from each account's available balance plus the day's scheduled income, and
    shrinks with every planned spend. Real people mostly skip what they can't afford.
    """

    def __init__(self, rng: random.Random, available_by_account: Mapping[str, int]) -> None:
        self._rng = rng
        self._remaining = dict(available_by_account)

    def remaining_cents(self, account_id: str) -> int:
        """Money left in the account for the rest of the day."""
        return self._remaining.get(account_id, 0)

    def try_spend(self, account_id: str, amount_cents: int) -> bool:
        """Return True if the customer goes ahead with this spend."""
        remaining = self.remaining_cents(account_id)
        if amount_cents <= remaining:
            self._remaining[account_id] = remaining - amount_cents
            return True
        return chance(self._rng, OVERSPEND_CHANCE)
