import random
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import CardEventType, CardType
from sentinel.domain.merchants import MerchantCategory
from sentinel.domain.transactions import Channel, EntryMode, TransactionAuthMethod
from sentinel.simulator.card_activity import HOME_COUNTRY
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.cash_out import Round
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.fraud.lost_stolen import (
    CALL_SECONDS,
    FIRST_USE_SECONDS,
    ONLINE_ABROAD_CATEGORIES,
    RETRIES,
    TAP_CENTS,
    THEFT_HOURS,
    LostStolen,
    LostStolenEpisode,
)
from sentinel.simulator.fraud.reports import plan_card_block
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.spending import ATM_STEP_CENTS

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)
RICH = 100_000_000  # far more than any daily limit


class FixedBalances:
    """Every account has the same amount available."""

    def __init__(self, available_cents: int) -> None:
        self.available = available_cents

    def available_cents(self, account_id: str) -> int:
        return self.available


@pytest.fixture(scope="module")
def scenario(reference_data: ReferenceData) -> LostStolen:
    return LostStolen(reference_data.merchants)


@pytest.fixture(scope="module")
def episodes(scenario: LostStolen, victims: list[Victim]) -> list[LostStolenEpisode]:
    rng = random.Random(3)
    return [
        scenario.plan(rng, victim, START, f"SCN-{number:07d}")
        for number, victim in enumerate(victims[:400], start=1)
    ]


def with_pin(episodes: list[LostStolenEpisode], is_pin_known: bool) -> list[LostStolenEpisode]:
    return [episode for episode in episodes if episode.is_pin_known is is_pin_known]


def round_drafts(
    episode: LostStolenEpisode, spree: Round, available_cents: int
) -> list[TransactionDraft]:
    """What the thief does in one round, leaving out the scripted attempts that day."""
    attempts = episode.act(to_sast(spree.start).date(), FixedBalances(available_cents)).attempts
    drafts = [d for d in attempts if isinstance(d, TransactionDraft)]
    return [d for d in drafts if d not in episode.scripted and d.event_time >= spree.start]


def play_all(episode: LostStolenEpisode, available_cents: int) -> list[list[Channel]]:
    """The channels used in each round, with the given balance."""
    return [
        [d.channel for d in round_drafts(episode, spree, available_cents)]
        for spree in episode.rounds
    ]


def test_the_card_is_stolen_in_the_day_and_reported_hours_later(
    episodes: list[LostStolenEpisode], victims: list[Victim]
) -> None:
    for episode, victim in zip(episodes, victims, strict=False):
        loss = episode.card_loss
        assert episode.card_id == loss.card_id
        assert episode.card in victim.cards
        assert episode.victim_customer_id == victim.customer.customer_id

        theft = to_sast(loss.lost_at)
        assert theft.date() == to_sast(START).date()
        assert THEFT_HOURS[0] <= theft.time() < THEFT_HOURS[1]
        delay = (loss.reported_lost_at - loss.lost_at).total_seconds()
        assert CALL_SECONDS[0] <= delay <= CALL_SECONDS[1]


def test_most_stolen_cards_are_debit_cards(
    episodes: list[LostStolenEpisode], victims: list[Victim]
) -> None:
    has_both = [
        e
        for e, v in zip(episodes, victims, strict=False)
        if len({c.card_type for c in v.cards}) > 1
    ]
    debit = [e for e in has_both if e.card.card_type is CardType.DEBIT]

    assert 0.75 < len(debit) / len(has_both) < 0.97


def test_rounds_run_until_the_call_with_a_second_after_midnight(
    episodes: list[LostStolenEpisode],
) -> None:
    for episode in episodes:
        loss = episode.card_loss
        first = episode.rounds[0]
        lead = (first.start - loss.lost_at).total_seconds()
        assert FIRST_USE_SECONDS[0] <= lead <= FIRST_USE_SECONDS[1]

        midnight = datetime.combine(to_sast(loss.lost_at).date() + timedelta(days=1), time(), SAST)
        assert first.end == min(loss.reported_lost_at, midnight)
        if len(episode.rounds) == 2:
            second = episode.rounds[1]
            assert midnight < second.start < second.end == loss.reported_lost_at
        assert episode.params["midnight_round"] is (len(episode.rounds) == 2)

    assert any(len(episode.rounds) == 2 for episode in episodes)


def test_the_thief_tries_again_after_the_block(episodes: list[LostStolenEpisode]) -> None:
    for episode in episodes:
        retries = [d for d in episode.scripted if d.event_time > episode.card_loss.reported_lost_at]
        assert RETRIES[0] <= len(retries) <= RETRIES[1]
        assert episode.planned_end() == retries[-1].event_time
        expected = Channel.ATM if episode.is_pin_known else Channel.CARD_PRESENT
        assert {d.channel for d in retries} == {expected}


def test_the_victim_withdraws_cash_where_the_card_is_swapped(
    episodes: list[LostStolenEpisode],
) -> None:
    for episode in with_pin(episodes, True):
        withdrawal = episode.victim_withdrawal
        assert withdrawal is not None
        assert withdrawal.card_id == episode.card_id
        assert withdrawal.merchant_id == episode.params["theft_atm_id"]
        assert withdrawal.event_time < episode.card_loss.lost_at
        assert withdrawal.merchant_id not in {atm.merchant_id for atm in episode.atms}

        actions = episode.act(to_sast(withdrawal.event_time).date(), FixedBalances(RICH))
        assert actions.genuine == [withdrawal]

    for episode in with_pin(episodes, False):
        assert episode.victim_withdrawal is None
        assert "theft_atm_id" not in episode.params


def test_with_the_pin_the_thief_drains_the_atm_limit_then_shops(
    episodes: list[LostStolenEpisode],
) -> None:
    for episode in with_pin(episodes, True):
        spree = episode.rounds[0]
        drafts = round_drafts(episode, spree, RICH)
        cash = [d for d in drafts if d.channel is Channel.ATM]
        shopping = [d for d in drafts if d.channel is Channel.CARD_PRESENT]

        assert cash
        assert all(d.amount_cents % ATM_STEP_CENTS == 0 for d in cash)
        assert sum(d.amount_cents for d in cash[:-1]) < episode.card.daily_atm_limit_cents
        assert all(d.auth_method is TransactionAuthMethod.PIN for d in shopping)
        assert all(d.entry_mode is EntryMode.CHIP for d in shopping)
        assert all(spree.start <= d.event_time < spree.end for d in cash + shopping)


def test_without_the_pin_the_thief_taps_small_amounts(episodes: list[LostStolenEpisode]) -> None:
    for episode in with_pin(episodes, False):
        spree = episode.rounds[0]
        taps = round_drafts(episode, spree, RICH)

        assert taps
        assert all(TAP_CENTS[0] <= d.amount_cents <= TAP_CENTS[1] for d in taps)
        assert all(d.auth_method is TransactionAuthMethod.NONE for d in taps)
        assert {d.entry_mode for d in taps} == {EntryMode.CONTACTLESS}


def test_an_empty_account_gets_one_attempt_per_round(episodes: list[LostStolenEpisode]) -> None:
    for episode in episodes:
        for channels in play_all(episode, 0):
            first_use = Channel.ATM if episode.is_pin_known else Channel.CARD_PRESENT
            assert channels.count(first_use) == 1


def test_a_round_never_runs_past_its_end(episodes: list[LostStolenEpisode]) -> None:
    episode = with_pin(episodes, True)[0]
    start = episode.rounds[0].start
    short = replace(episode, rounds=(Round(start, start + timedelta(seconds=1)),))

    in_round = round_drafts(short, short.rounds[0], RICH)

    assert [d.event_time for d in in_round] == [start]


def test_rounds_replay_the_same_way(episodes: list[LostStolenEpisode]) -> None:
    for episode in episodes[:50]:
        assert play_all(episode, 500_000) == play_all(episode, 500_000)


def test_some_cards_are_also_used_online_abroad(
    episodes: list[LostStolenEpisode], reference_data: ReferenceData
) -> None:
    merchants = {m.merchant_id: m for m in reference_data.merchants}
    abroad = [e for e in episodes if e.params["online_abroad"]]

    assert 0 < len(abroad) < len(episodes) / 2
    for episode in abroad:
        online = [d for d in episode.scripted if d.channel is Channel.CARD_NOT_PRESENT]
        assert online
        assert all(d.event_time >= episode.rounds[0].start for d in online)
        for draft in online:
            assert draft.merchant_id is not None
            merchant = merchants[draft.merchant_id]
            assert merchant.country_code != HOME_COUNTRY
            assert merchant.category in ONLINE_ABROAD_CATEGORIES


def test_the_episode_acts_on_the_theft_call_and_retry_days(
    episodes: list[LostStolenEpisode],
) -> None:
    for episode in episodes:
        loss = episode.card_loss
        days = {to_sast(loss.lost_at).date(), to_sast(episode.planned_end()).date()}
        assert days <= episode.days()


def test_only_customers_with_a_debit_card_can_be_targeted(
    scenario: LostStolen, victims: list[Victim]
) -> None:
    victim = victims[0]
    no_debit = replace(
        victim, cards=tuple(c for c in victim.cards if c.card_type is not CardType.DEBIT)
    )

    assert scenario.can_target(victim)
    assert not scenario.can_target(no_debit)


def test_needs_foreign_online_merchants(reference_data: ReferenceData) -> None:
    shops = [m for m in reference_data.merchants if not m.is_online]
    with pytest.raises(SimulationError, match="foreign online"):
        LostStolen(shops)


def test_needs_nearby_atms(reference_data: ReferenceData, victims: list[Victim]) -> None:
    no_atms = [m for m in reference_data.merchants if m.category is not MerchantCategory.ATM]
    with pytest.raises(SimulationError, match="atm"):
        LostStolen(no_atms).plan(random.Random(1), victims[0], START, "SCN-0000001")


def test_the_call_blocks_the_card_even_if_no_fraud_is_claimed(
    episodes: list[LostStolenEpisode],
) -> None:
    episode = episodes[0]
    claimed_at = episode.planned_end() + timedelta(days=1)

    for reported_at in (claimed_at, None):
        block = plan_card_block(episode, reported_at)
        assert block is not None
        assert (block.card_id, block.blocked_at) == (
            episode.card_id,
            episode.card_loss.reported_lost_at,
        )
        assert block.event_type is CardEventType.REPORTED_LOST
