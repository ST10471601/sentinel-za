import random
from datetime import UTC, datetime

import pytest

from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import CardType
from sentinel.domain.labels import FraudType
from sentinel.domain.transactions import Channel
from sentinel.simulator.card_activity import HOME_COUNTRY
from sentinel.simulator.fraud import card_not_present
from sentinel.simulator.fraud.card_not_present import (
    CATEGORIES_BY_CARD_TYPE,
    MAIN_AMOUNT_CENTS,
    TEST_AMOUNT_CENTS,
    CardNotPresent,
)
from sentinel.simulator.fraud.episodes import ScriptedEpisode, Victim
from sentinel.simulator.reference_data import ReferenceData

START = datetime(2026, 1, 14, 18, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def scenario(reference_data: ReferenceData) -> CardNotPresent:
    return CardNotPresent(reference_data.merchants)


@pytest.fixture(scope="module")
def episodes(scenario: CardNotPresent, victims: list[Victim]) -> list[ScriptedEpisode]:
    rng = random.Random(5)
    return [
        scenario.plan(rng, victim, START, f"SCN-{number:07d}")
        for number, victim in enumerate(victims[:400], start=1)
    ]


def test_episode_is_test_charges_then_a_burst_on_one_card(
    episodes: list[ScriptedEpisode], victims: list[Victim]
) -> None:
    for episode, victim in zip(episodes, victims, strict=False):
        drafts = episode.drafts
        test_count = int(episode.params["test_charges"])
        assert len(drafts) == test_count + int(episode.params["charges"])
        assert episode.fraud_type is FraudType.CARD_NOT_PRESENT
        assert episode.victim_customer_id == victim.customer.customer_id

        assert drafts[0].event_time == START
        assert [d.event_time for d in drafts] == sorted(d.event_time for d in drafts)
        assert {d.channel for d in drafts} == {Channel.CARD_NOT_PRESENT}
        assert len({d.card_id for d in drafts}) == 1
        assert drafts[0].card_id in {card.card_id for card in victim.cards}

        for draft in drafts[:test_count]:
            assert TEST_AMOUNT_CENTS[0] <= draft.amount_cents <= TEST_AMOUNT_CENTS[1]
        for draft in drafts[test_count:]:
            assert MAIN_AMOUNT_CENTS[0] <= draft.amount_cents <= MAIN_AMOUNT_CENTS[1]


def test_most_bursts_are_abroad_in_the_sabric_categories(
    episodes: list[ScriptedEpisode], reference_data: ReferenceData
) -> None:
    merchants = {m.merchant_id: m for m in reference_data.merchants}
    foreign = 0
    for episode in episodes:
        used = [merchants[str(d.merchant_id)] for d in episode.drafts]
        is_foreign = used[0].country_code != HOME_COUNTRY
        foreign += is_foreign
        assert episode.params["foreign"] is is_foreign
        card_type = CardType(str(episode.params["card_type"]))
        assert {m.category for m in used} <= set(CATEGORIES_BY_CARD_TYPE[card_type])
    assert 0.55 < foreign / len(episodes) < 0.75


def test_credit_cards_are_used_only_when_the_victim_has_one(
    episodes: list[ScriptedEpisode], victims: list[Victim]
) -> None:
    on_credit = 0
    with_credit = 0
    for episode, victim in zip(episodes, victims, strict=False):
        has_credit = any(card.card_type is CardType.CREDIT for card in victim.cards)
        with_credit += has_credit
        if episode.params["card_type"] == CardType.CREDIT.value:
            assert has_credit
            on_credit += 1
    assert 0.45 < on_credit / with_credit < 0.75


def test_victim_needs_a_card(scenario: CardNotPresent, victims: list[Victim]) -> None:
    assert scenario.can_target(victims[0])
    assert not scenario.can_target(Victim(victims[0].customer, (), victims[0].payments))


def test_victim_without_a_debit_card_is_an_error(
    scenario: CardNotPresent, victims: list[Victim], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(card_not_present, "CREDIT_CARD_SHARE", 0.0)
    victim = next(v for v in victims if any(c.card_type is CardType.CREDIT for c in v.cards))
    credit_only = tuple(c for c in victim.cards if c.card_type is CardType.CREDIT)

    with pytest.raises(SimulationError, match="no debit card"):
        scenario.plan(
            random.Random(1),
            Victim(victim.customer, credit_only, victim.payments),
            START,
            "SCN-0000001",
        )


def test_missing_merchants_are_an_error(victims: list[Victim]) -> None:
    no_merchants = CardNotPresent([])
    with pytest.raises(SimulationError, match="no online merchants"):
        no_merchants.plan(random.Random(1), victims[0], START, "SCN-0000001")
