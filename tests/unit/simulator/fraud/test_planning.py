import random
from collections import Counter
from datetime import UTC, date, datetime

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.labels import FraudType
from sentinel.simulator.fraud.episodes import ScriptedEpisode, Victim
from sentinel.simulator.fraud.planning import (
    FRAUD_MIX,
    FraudSettings,
    episode_count,
    plan_episodes,
)
from sentinel.simulator.identifiers import IdSequence

PERIOD = (date(2026, 1, 1), date(2026, 3, 31))


class FakeScenario:
    """Records when each episode starts; targets only customers it is told to."""

    def __init__(self, fraud_type: FraudType, targets_anyone: bool = True) -> None:
        self.fraud_type = fraud_type
        self._targets_anyone = targets_anyone
        self.starts: list[datetime] = []

    def can_target(self, victim: Victim) -> bool:
        return self._targets_anyone

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> ScriptedEpisode:
        self.starts.append(start)
        return ScriptedEpisode(
            scenario_id, self.fraud_type, victim.customer.customer_id, {}, drafts=()
        )


def test_default_rate_gives_about_fifteen_episodes_per_thousand_customers_a_month() -> None:
    assert episode_count(FraudSettings(), customer_count=2_000, months=3) == 90
    assert episode_count(FraudSettings(0.0), customer_count=2_000, months=3) == 0


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"episodes_per_thousand_customers_per_month": -1.0}, "must not be negative"),
        ({"unreported_share": 1.5}, "between 0 and 1"),
    ],
)
def test_settings_are_checked(settings: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FraudSettings(**settings)


def test_each_episode_has_its_own_victim_id_and_a_start_in_the_period(
    victims: list[Victim],
) -> None:
    scenario = FakeScenario(FraudType.CARD_NOT_PRESENT)
    episodes = plan_episodes(random.Random(1), [scenario], victims, PERIOD, 50, IdSequence("SCN"))

    assert [e.scenario_id for e in episodes] == [f"SCN-{n:07d}" for n in range(1, 51)]
    assert len({e.victim_customer_id for e in episodes}) == 50
    for start in scenario.starts:
        assert start.tzinfo is UTC
        assert PERIOD[0] <= to_sast(start).date() <= PERIOD[1]


def test_scenarios_are_mixed_in_their_fraud_mix_proportions(victims: list[Victim]) -> None:
    common = FakeScenario(FraudType.CARD_NOT_PRESENT)  # 0.30
    rare = FakeScenario(FraudType.CARD_PRESENT_COUNTERFEIT)  # 0.02
    episodes = plan_episodes(
        random.Random(2), [common, rare], victims, PERIOD, 640, IdSequence("SCN")
    )

    counts = Counter(e.fraud_type for e in episodes)
    expected_rare = 640 * FRAUD_MIX[rare.fraud_type] / (FRAUD_MIX[common.fraud_type] + 0.02)
    assert counts[rare.fraud_type] == pytest.approx(expected_rare, abs=15)


def test_scenarios_only_get_victims_they_can_target(victims: list[Victim]) -> None:
    nobody = FakeScenario(FraudType.SIM_SWAP_TAKEOVER, targets_anyone=False)
    assert plan_episodes(random.Random(3), [nobody], victims, PERIOD, 5, IdSequence("SCN")) == []


def test_no_scenarios_or_no_episodes_plan_nothing(victims: list[Victim]) -> None:
    scenario = FakeScenario(FraudType.CARD_NOT_PRESENT)
    assert plan_episodes(random.Random(4), [], victims, PERIOD, 5, IdSequence("SCN")) == []
    assert plan_episodes(random.Random(4), [scenario], victims, PERIOD, 0, IdSequence("SCN")) == []


def test_starts_cover_the_whole_last_day(victims: list[Victim]) -> None:
    scenario = FakeScenario(FraudType.CARD_NOT_PRESENT)
    one_day = (date(2026, 1, 1), date(2026, 1, 1))
    plan_episodes(random.Random(5), [scenario], victims, one_day, 200, IdSequence("SCN"))

    hours = {to_sast(start).hour for start in scenario.starts}
    assert hours == set(range(24))
    assert max(scenario.starts) < datetime(2026, 1, 1, 22, tzinfo=UTC)  # SA midnight
