"""Decide how many fraud episodes happen, of which kind, to whom and when."""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST
from sentinel.domain.labels import FraudType
from sentinel.simulator.fraud.episodes import Episode, Scenario, Victim
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.randomness import pick, random_datetime_between

# Share of episodes by type, from the SABRIC 2025 research (SZ-3). Mule layering is not
# here: it follows the money other episodes steal.
FRAUD_MIX: dict[FraudType, float] = {
    FraudType.CARD_NOT_PRESENT: 0.30,
    FraudType.APP_VISHING: 0.20,
    FraudType.CARD_PRESENT_LOST_STOLEN: 0.15,
    FraudType.PAYSHAP_DRAIN: 0.10,
    FraudType.APP_REMOTE_ACCESS: 0.08,
    FraudType.SIM_SWAP_TAKEOVER: 0.07,
    FraudType.SUPPLIER_MANDATE: 0.03,
    FraudType.CARD_PRESENT_COUNTERFEIT: 0.02,
}


@dataclass(frozen=True, slots=True)
class FraudSettings:
    """How much fraud to inject. The default lands near 0.2% of transactions."""

    episodes_per_thousand_customers_per_month: float = 15.0
    unreported_share: float = 0.05  # fraud the bank never hears about

    def __post_init__(self) -> None:
        if self.episodes_per_thousand_customers_per_month < 0:
            raise ValueError("episodes_per_thousand_customers_per_month must not be negative")
        if not 0 <= self.unreported_share <= 1:
            raise ValueError("unreported_share must be between 0 and 1")


def episode_count(settings: FraudSettings, customer_count: int, months: int) -> int:
    """How many episodes a run of this size gets."""
    rate = settings.episodes_per_thousand_customers_per_month
    return round(rate * customer_count / 1_000 * months)


def plan_episodes(
    rng: random.Random,
    scenarios: Sequence[Scenario],
    victims: Sequence[Victim],
    period: tuple[date, date],
    count: int,
    scenario_ids: IdSequence,
) -> list[Episode]:
    """Plan ``count`` episodes, each against a different victim, starting within ``period``.

    Only the given scenarios are used, in their FRAUD_MIX proportions. An episode that runs
    past the last day is cut off when the simulation ends, as real data would be.
    """
    if not scenarios or count == 0:
        return []

    weights = {scenario: FRAUD_MIX[scenario.fraud_type] for scenario in scenarios}
    first_day, last_day = period
    window_start = datetime.combine(first_day, time(), tzinfo=SAST)
    window_end = datetime.combine(last_day + timedelta(days=1), time(), tzinfo=SAST)
    untouched = list(victims)  # one episode per victim keeps scenarios easy to tell apart

    episodes: list[Episode] = []
    for _ in range(count):
        scenario = pick(rng, weights)
        candidates = [victim for victim in untouched if scenario.can_target(victim)]
        if not candidates:
            continue
        victim = rng.choice(candidates)
        untouched.remove(victim)
        start = random_datetime_between(rng, window_start, window_end).astimezone(UTC)
        episodes.append(scenario.plan(rng, victim, start, scenario_ids.next_id()))
    return episodes
