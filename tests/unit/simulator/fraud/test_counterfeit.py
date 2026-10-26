import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import CardEventType, CardType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.domain.transactions import Channel, EntryMode, TransactionAuthMethod
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.cash_out import Round
from sentinel.simulator.fraud.counterfeit import (
    CREDIT_SKIM_CATEGORIES,
    DAYS_USED,
    FIRST_USE_DAYS,
    PURCHASE_CENTS,
    ROUND_HOURS,
    Counterfeit,
    CounterfeitEpisode,
)
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.fraud.reports import plan_card_block
from sentinel.simulator.reference_data import ReferenceData

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)
RICH = 100_000_000  # far more than any daily limit


class FixedBalances:
    """Every account has the same amount available."""

    def __init__(self, available_cents: int) -> None:
        self.available = available_cents

    def available_cents(self, account_id: str) -> int:
        return self.available


@pytest.fixture(scope="module")
def scenario(reference_data: ReferenceData) -> Counterfeit:
    return Counterfeit(reference_data.merchants)


@pytest.fixture(scope="module")
def episodes(scenario: Counterfeit, victims: list[Victim]) -> list[CounterfeitEpisode]:
    rng = random.Random(5)
    return [
        scenario.plan(rng, victim, START, f"SCN-{number:07d}")
        for number, victim in enumerate(victims[:400], start=1)
    ]


@pytest.fixture(scope="module")
def merchants(reference_data: ReferenceData) -> dict[str, Merchant]:
    return {merchant.merchant_id: merchant for merchant in reference_data.merchants}


def round_drafts(
    episode: CounterfeitEpisode, spree: Round, available_cents: int = RICH
) -> list[TransactionDraft]:
    """What the clone does on the round's day; there is one round a day."""
    attempts = episode.act(to_sast(spree.start).date(), FixedBalances(available_cents)).attempts
    return [a for a in attempts if isinstance(a, TransactionDraft)]


def test_the_card_is_skimmed_while_the_victim_uses_it(
    episodes: list[CounterfeitEpisode], victims: list[Victim], merchants: dict[str, Merchant]
) -> None:
    for episode, victim in zip(episodes, victims, strict=False):
        skim = episode.skim
        assert skim.merchant_id is not None
        assert episode.card in victim.cards
        assert episode.victim_customer_id == victim.customer.customer_id
        assert skim.card_id == episode.card_id
        assert skim.merchant_id == episode.params["skim_merchant_id"]
        assert skim.event_time.isoformat() == episode.params["skimmed_at"]
        assert to_sast(skim.event_time).date() == to_sast(START).date()
        assert (skim.entry_mode, skim.auth_method) == (EntryMode.CHIP, TransactionAuthMethod.PIN)

        category = merchants[skim.merchant_id].category
        if episode.card.card_type is CardType.DEBIT:
            assert category is MerchantCategory.ATM
        else:
            assert category in CREDIT_SKIM_CATEGORIES

        actions = episode.act(to_sast(skim.event_time).date(), FixedBalances(RICH))
        assert actions.genuine == [skim]
        assert not actions.attempts


def test_most_cloned_cards_are_debit_cards(
    episodes: list[CounterfeitEpisode], victims: list[Victim]
) -> None:
    has_both = [
        e
        for e, v in zip(episodes, victims, strict=False)
        if len({c.card_type for c in v.cards}) > 1
    ]
    debit = [e for e in has_both if e.card.card_type is CardType.DEBIT]

    assert 0.6 < len(debit) / len(has_both) < 0.9


def test_the_clone_is_used_days_later_on_days_in_a_row(
    episodes: list[CounterfeitEpisode],
) -> None:
    for episode in episodes:
        skim_day = to_sast(episode.skim.event_time).date()
        days = [to_sast(spree.start).date() for spree in episode.rounds]
        wait = (days[0] - skim_day).days

        assert FIRST_USE_DAYS[0] <= wait <= FIRST_USE_DAYS[1]
        assert wait == episode.params["first_use_after_days"]
        assert DAYS_USED[0] <= len(days) <= DAYS_USED[1]
        assert len(days) == episode.params["days_used"]
        assert days == [days[0] + timedelta(days=n) for n in range(len(days))]
        assert episode.days() == {skim_day, *days}


def test_each_round_starts_in_the_day_and_ends_at_midnight(
    episodes: list[CounterfeitEpisode],
) -> None:
    for episode in episodes:
        for spree in episode.rounds:
            start = to_sast(spree.start)
            assert ROUND_HOURS[0] <= start.time() < ROUND_HOURS[1]
            assert spree.end == datetime.combine(start.date() + timedelta(days=1), time(), SAST)
        assert episode.planned_end() == episode.rounds[-1].end


def test_the_clone_is_used_in_another_city(
    episodes: list[CounterfeitEpisode], victims: list[Victim]
) -> None:
    used = 0
    in_city = 0
    for episode, victim in zip(episodes, victims, strict=False):
        city = episode.params["cash_out_city"]
        assert city != victim.customer.home_city
        places = [*episode.atms, *episode.shops]
        used += len(places)
        in_city += sum(merchant.city == city for merchant in places)

    # Small towns have too few shops, so a few of the nearest are in the next town.
    assert in_city / used > 0.75


def test_big_cities_see_more_clones_than_small_towns(
    episodes: list[CounterfeitEpisode],
) -> None:
    cities = Counter(episode.params["cash_out_city"] for episode in episodes)
    assert cities["Johannesburg"] > cities["Upington"]
    assert cities["Cape Town"] > cities["Stellenbosch"]


def test_a_round_takes_cash_then_shops_with_the_swiped_clone(
    episodes: list[CounterfeitEpisode], merchants: dict[str, Merchant]
) -> None:
    shopped = 0
    for episode in episodes[:100]:
        spree = episode.rounds[0]
        drafts = round_drafts(episode, spree)
        cash = [d for d in drafts if d.channel is Channel.ATM]
        shopping = [d for d in drafts if d.channel is Channel.CARD_PRESENT]

        assert cash and len(cash) + len(shopping) == len(drafts)
        assert all(d.card_id == episode.card_id for d in drafts)
        assert all(d.entry_mode is EntryMode.MAGSTRIPE for d in drafts)
        assert all(d.auth_method is TransactionAuthMethod.PIN for d in drafts)
        assert all(spree.start <= d.event_time < spree.end for d in drafts)
        for draft in shopping:
            assert draft.merchant_id is not None
            low, high = PURCHASE_CENTS[merchants[draft.merchant_id].category]
            assert low <= draft.amount_cents <= high
            assert draft.event_time > cash[-1].event_time
        shopped += bool(shopping)

    assert shopped


def test_a_round_never_runs_past_its_end(episodes: list[CounterfeitEpisode]) -> None:
    episode = episodes[0]
    start = episode.rounds[0].start
    short = replace(episode, rounds=(Round(start, start + timedelta(seconds=1)),))

    in_round = round_drafts(short, short.rounds[0])

    assert [d.event_time for d in in_round] == [start]


def test_rounds_replay_the_same_way(episodes: list[CounterfeitEpisode]) -> None:
    for episode in episodes[:50]:
        for spree in episode.rounds:
            assert round_drafts(episode, spree, 500_000) == round_drafts(episode, spree, 500_000)


def test_the_victim_keeps_the_card_until_the_report_blocks_it(
    episodes: list[CounterfeitEpisode],
) -> None:
    episode = episodes[0]
    reported_at = episode.planned_end() + timedelta(days=5)

    block = plan_card_block(episode, reported_at)

    assert episode.card_loss is None
    assert block is not None
    assert (block.card_id, block.blocked_at) == (episode.card_id, reported_at)
    assert block.event_type is CardEventType.BLOCKED
    assert plan_card_block(episode, None) is None


def test_only_customers_with_a_debit_card_can_be_targeted(
    scenario: Counterfeit, victims: list[Victim]
) -> None:
    victim = victims[0]
    no_debit = replace(
        victim, cards=tuple(c for c in victim.cards if c.card_type is not CardType.DEBIT)
    )

    assert scenario.can_target(victim)
    assert not scenario.can_target(no_debit)


def test_needs_atms(reference_data: ReferenceData, victims: list[Victim]) -> None:
    no_atms = [m for m in reference_data.merchants if m.category is not MerchantCategory.ATM]
    with pytest.raises(SimulationError, match="atm"):
        Counterfeit(no_atms).plan(random.Random(1), victims[0], START, "SCN-0000001")
