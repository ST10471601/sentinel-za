import random
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

import pytest

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import CardEventType, CardType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.domain.transactions import Channel, EntryMode, TransactionAuthMethod
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud import counterfeit
from sentinel.simulator.fraud.cash_out import Round
from sentinel.simulator.fraud.counterfeit import (
    CARD_GAP_SECONDS,
    CREDIT_SKIM_CATEGORIES,
    DAYS_USED,
    FIRST_USE_DAYS,
    PURCHASE_CENTS,
    ROUND_HOURS,
    Counterfeit,
    CounterfeitEpisode,
)
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.fraud.planning import plan_episodes
from sentinel.simulator.fraud.reports import plan_card_block
from sentinel.simulator.fraud.skimming_sites import CLONED_PER_SITE
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.reference_data import ReferenceData

PERIOD = (date(2026, 1, 1), date(2026, 3, 31))
START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)
RICH = 100_000_000  # far more than any daily limit


class FixedBalances:
    """Every account has the same amount available."""

    def __init__(self, available_cents: int) -> None:
        self.available = available_cents

    def available_cents(self, account_id: str) -> int:
        return self.available


@pytest.fixture(scope="module")
def episodes(reference_data: ReferenceData, victims: list[Victim]) -> list[CounterfeitEpisode]:
    """Planned the way a run plans them, so each site's victims are chosen one after another."""
    scenario = Counterfeit(reference_data.merchants)
    planned = plan_episodes(random.Random(5), [scenario], victims, PERIOD, 300, IdSequence("SCN"))
    return [episode for episode in planned if isinstance(episode, CounterfeitEpisode)]


@pytest.fixture(scope="module")
def merchants(reference_data: ReferenceData) -> dict[str, Merchant]:
    return {merchant.merchant_id: merchant for merchant in reference_data.merchants}


@pytest.fixture(scope="module")
def victim_by_id(victims: list[Victim]) -> dict[str, Victim]:
    return {victim.customer.customer_id: victim for victim in victims}


def site_batches(episodes: list[CounterfeitEpisode]) -> list[list[CounterfeitEpisode]]:
    """Episodes from skimming sites, one list per site."""
    batches: dict[tuple[object, object], list[CounterfeitEpisode]] = defaultdict(list)
    for episode in episodes:
        if "site_opens" in episode.params:
            site = (episode.params["skim_merchant_id"], episode.params["site_opens"])
            batches[site].append(episode)
    return list(batches.values())


def round_drafts(
    episode: CounterfeitEpisode, spree: Round, available_cents: int = RICH
) -> list[TransactionDraft]:
    """What the clone does on the round's day; there is one round a day."""
    attempts = episode.act(to_sast(spree.start).date(), FixedBalances(available_cents)).attempts
    return [a for a in attempts if isinstance(a, TransactionDraft)]


def test_debit_cards_are_skimmed_at_sites_and_credit_cards_at_shops(
    episodes: list[CounterfeitEpisode],
    victim_by_id: dict[str, Victim],
    merchants: dict[str, Merchant],
) -> None:
    for episode in episodes:
        skim = episode.skim
        assert skim.merchant_id is not None
        assert episode.card in victim_by_id[episode.victim_customer_id].cards
        assert skim.card_id == episode.card_id
        assert skim.merchant_id == episode.params["skim_merchant_id"]
        assert skim.event_time.isoformat() == episode.params["skimmed_at"]
        assert (skim.entry_mode, skim.auth_method) == (EntryMode.CHIP, TransactionAuthMethod.PIN)

        category = merchants[skim.merchant_id].category
        if episode.card.card_type is CardType.DEBIT:
            assert category is MerchantCategory.ATM
            assert "site_opens" in episode.params
        else:
            assert category in CREDIT_SKIM_CATEGORIES
            assert "site_opens" not in episode.params

        actions = episode.act(to_sast(skim.event_time).date(), FixedBalances(RICH))
        assert actions.genuine == [skim]
        assert not actions.attempts


def test_about_a_quarter_of_clones_are_credit_cards(episodes: list[CounterfeitEpisode]) -> None:
    credit = sum(episode.card.card_type is CardType.CREDIT for episode in episodes)
    assert 0.15 < credit / len(episodes) < 0.33


def test_a_site_skims_several_regulars_while_it_is_fitted(
    episodes: list[CounterfeitEpisode], victim_by_id: dict[str, Victim]
) -> None:
    batches = site_batches(episodes)
    sizes = [len(batch) for batch in batches]

    assert max(sizes) <= CLONED_PER_SITE[1]
    assert sum(size >= CLONED_PER_SITE[0] for size in sizes) > len(sizes) / 2
    for batch in batches:
        for episode in batch:
            favourites = victim_by_id[episode.victim_customer_id].favourite_atms
            assert episode.params["skim_merchant_id"] in {atm.merchant_id for atm in favourites}
            opens = date.fromisoformat(str(episode.params["site_opens"]))
            closes = date.fromisoformat(str(episode.params["site_closes"]))
            assert opens <= to_sast(episode.skim.event_time).date() <= closes


def test_a_sites_cards_are_cashed_out_together_card_after_card(
    episodes: list[CounterfeitEpisode],
) -> None:
    for batch in site_batches(episodes):
        first = batch[0]
        closes = date.fromisoformat(str(first.params["site_closes"]))
        wait = (to_sast(first.rounds[0].start).date() - closes).days

        assert FIRST_USE_DAYS[0] <= wait <= FIRST_USE_DAYS[1]
        assert len({episode.params["cash_out_city"] for episode in batch}) == 1
        assert len({episode.atms for episode in batch}) == 1
        assert len({tuple(r.end for r in episode.rounds) for episode in batch}) == 1
        starts = [episode.rounds[0].start for episode in batch]
        for earlier, later in pairwise(starts):
            gap = (later - earlier).total_seconds()
            assert CARD_GAP_SECONDS[0] <= gap <= CARD_GAP_SECONDS[1]


def test_a_crews_clones_pass_through_the_same_atms_within_minutes(
    episodes: list[CounterfeitEpisode],
) -> None:
    batches = [batch for batch in site_batches(episodes) if len(batch) > 1]
    shared = 0
    for batch in batches:
        visits = [
            (draft.merchant_id, draft.event_time, draft.card_id)
            for episode in batch
            for draft in round_drafts(episode, episode.rounds[0])
            if draft.channel is Channel.ATM
        ]
        shared += any(
            atm == other_atm
            and card != other_card
            and abs(moment - other_moment) <= timedelta(minutes=15)
            for atm, moment, card in visits
            for other_atm, other_moment, other_card in visits
        )

    assert shared > len(batches) / 2


def test_the_clone_is_used_days_later_on_days_in_a_row(
    episodes: list[CounterfeitEpisode],
) -> None:
    for episode in episodes:
        skim_day = to_sast(episode.skim.event_time).date()
        days = [to_sast(spree.start).date() for spree in episode.rounds]
        wait = (days[0] - skim_day).days

        assert wait == episode.params["first_use_after_days"]
        assert DAYS_USED[0] <= len(days) <= DAYS_USED[1]
        assert len(days) == episode.params["days_used"]
        assert days == [days[0] + timedelta(days=n) for n in range(len(days))]
        assert episode.days() == {skim_day, *days}
        if episode.card.card_type is CardType.CREDIT:
            assert FIRST_USE_DAYS[0] <= wait <= FIRST_USE_DAYS[1]


def test_each_round_starts_in_shop_hours_and_ends_at_midnight(
    episodes: list[CounterfeitEpisode],
) -> None:
    for episode in episodes:
        for spree in episode.rounds:
            start = to_sast(spree.start)
            assert start.time() >= ROUND_HOURS[0]
            assert spree.end == datetime.combine(start.date() + timedelta(days=1), time(), SAST)
            if episode.card.card_type is CardType.CREDIT:  # alone, so set out at once
                assert start.time() < ROUND_HOURS[1]
        assert episode.planned_end() == episode.rounds[-1].end


def test_the_clone_is_used_in_another_city(
    episodes: list[CounterfeitEpisode], victim_by_id: dict[str, Victim]
) -> None:
    used = 0
    in_city = 0
    for episode in episodes:
        city = episode.params["cash_out_city"]
        assert city != victim_by_id[episode.victim_customer_id].customer.home_city
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


def debit_only(victims: list[Victim]) -> list[Victim]:
    return [v for v in victims if all(c.card_type is CardType.DEBIT for c in v.cards)]


def test_the_episode_starts_with_the_first_cash_out(
    reference_data: ReferenceData, victims: list[Victim], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(counterfeit, "DEBIT_CARD_SHARE", 1.0)
    scenario = Counterfeit(reference_data.merchants)
    rng = random.Random(3)
    first_day = to_sast(START).date()
    holder = next(v for v in victims if any(c.card_type is CardType.CREDIT for c in v.cards))

    at_shop = scenario.plan(rng, holder, START, "SCN-0000001")  # not chosen for a site
    regular = scenario.choose_victim(rng, victims, START)
    assert regular is not None
    at_site = scenario.plan(rng, regular, START, "SCN-0000002")

    for episode in (at_shop, at_site):
        assert to_sast(episode.rounds[0].start).date() == first_day
        assert to_sast(episode.skim.event_time).date() < first_day
    assert date.fromisoformat(str(at_site.params["site_closes"])) < first_day


def test_with_no_credit_card_holders_a_site_regular_is_chosen(
    reference_data: ReferenceData, victims: list[Victim], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(counterfeit, "DEBIT_CARD_SHARE", 0.0)
    scenario = Counterfeit(reference_data.merchants)

    victim = scenario.choose_victim(random.Random(1), debit_only(victims), START)

    assert victim is not None
    episode = scenario.plan(random.Random(2), victim, START, "SCN-0000001")
    assert "site_opens" in episode.params


def test_nobody_is_chosen_when_no_one_uses_an_atm(
    reference_data: ReferenceData, victims: list[Victim], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(counterfeit, "DEBIT_CARD_SHARE", 1.0)
    no_atms = [replace(victim, favourite_atms=()) for victim in victims[:50]]

    chosen = Counterfeit(reference_data.merchants).choose_victim(random.Random(1), no_atms, START)

    assert chosen is None


def test_a_shop_skim_needs_a_credit_card(
    reference_data: ReferenceData, victims: list[Victim]
) -> None:
    scenario = Counterfeit(reference_data.merchants)
    with pytest.raises(SimulationError, match="no credit card"):
        scenario.plan(random.Random(1), debit_only(victims)[0], START, "SCN-0000001")


def test_needs_atms(reference_data: ReferenceData, victims: list[Victim]) -> None:
    no_atms = [m for m in reference_data.merchants if m.category is not MerchantCategory.ATM]
    with pytest.raises(SimulationError, match="atm"):
        plan_episodes(
            random.Random(1), [Counterfeit(no_atms)], victims, PERIOD, 5, IdSequence("SCN")
        )
