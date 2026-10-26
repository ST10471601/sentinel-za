"""Skimming sites: an ATM fitted with a skimmer for a few weeks, catching its regulars' cards."""

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sentinel.domain.merchants import Merchant
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.randomness import pick
from sentinel.simulator.spending import pick_event_time

# Simulation assumptions.
SITE_DAYS = (7, 21)  # taken off again before the bank or the ATM's owner notices
CLONED_PER_SITE = (3, 6)  # the skimmer catches more cards; these are the ones cashed out


@dataclass(frozen=True, slots=True)
class SkimmingSite:
    """An ATM with a skimmer fitted, from the day it opens to the last day it is in place."""

    atm: Merchant
    opens: date
    closes: date
    cards_cloned: int

    def regulars(self, candidates: Sequence[Victim]) -> list[Victim]:
        """The candidates who use this ATM regularly."""
        atm_id = self.atm.merchant_id
        return [v for v in candidates if any(a.merchant_id == atm_id for a in v.favourite_atms)]

    def skim_time(self, rng: random.Random) -> datetime:
        """When a regular takes cash here while the skimmer is fitted."""
        day = self.opens + timedelta(days=rng.randint(0, (self.closes - self.opens).days))
        return pick_event_time(rng, day)


def open_site(
    rng: random.Random, candidates: Sequence[Victim], closes: date
) -> SkimmingSite | None:
    """A skimmer at a busy ATM until ``closes``: more regulars, likelier. None without ATMs."""
    atms: dict[str, Merchant] = {}
    regulars: Counter[str] = Counter()
    for victim in candidates:
        for atm in victim.favourite_atms:
            atms[atm.merchant_id] = atm
            regulars[atm.merchant_id] += 1
    if not regulars:
        return None

    atm = atms[pick(rng, regulars)]
    opens = closes - timedelta(days=rng.randint(*SITE_DAYS) - 1)
    return SkimmingSite(atm, opens, closes, rng.randint(*CLONED_PER_SITE))
