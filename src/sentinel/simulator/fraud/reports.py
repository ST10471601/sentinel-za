"""When the bank hears about each fraud episode, decided when the episode is planned.

Deciding up front lets the bank act on a report during the run, such as blocking a card.
"""

import random
from datetime import datetime, timedelta

from sentinel.domain.labels import FraudType
from sentinel.simulator.fraud.episodes import Episode
from sentinel.simulator.randomness import chance

SECONDS_PER_DAY = 86_400

# Days from the planned end of an episode until the bank hears about it (simulation assumptions).
REPORT_DELAY_DAYS: dict[FraudType, tuple[float, float]] = {
    FraudType.CARD_NOT_PRESENT: (2, 30),  # spotted on the statement, then a chargeback
    FraudType.CARD_PRESENT_LOST_STOLEN: (0, 1),  # the victim misses the card quickly
    FraudType.CARD_PRESENT_COUNTERFEIT: (3, 30),  # the victim still has the real card
    FraudType.SIM_SWAP_TAKEOVER: (0, 3),  # the phone suddenly has no signal
    FraudType.APP_VISHING: (0, 2),
    FraudType.APP_REMOTE_ACCESS: (0, 2),
    FraudType.SUPPLIER_MANDATE: (7, 45),  # found when the real supplier asks to be paid
    FraudType.PAYSHAP_DRAIN: (0, 2),
    FraudType.MULE_LAYERING: (7, 60),  # traced back from the victims' claims
}


def plan_reported_at(
    rng: random.Random, episode: Episode, unreported_share: float
) -> datetime | None:
    """When the bank hears about ``episode``, or None if it never does."""
    if chance(rng, unreported_share):
        return None
    low, high = REPORT_DELAY_DAYS[episode.fraud_type]
    delay = round(rng.uniform(low, high) * SECONDS_PER_DAY)
    return episode.planned_end() + timedelta(seconds=delay)
