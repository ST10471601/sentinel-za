import itertools
import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.beneficiaries import BeneficiaryEventType
from sentinel.domain.customers import BankingChannel
from sentinel.domain.sessions import SessionAuthMethod
from sentinel.domain.transactions import Channel, TransactionAuthMethod
from sentinel.simulator.beneficiaries import OTHER_BANKS, PERSONAL_PAYEE_NAMES
from sentinel.simulator.devices import LINK_METHOD_BY_PLATFORM
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.account_takeover import (
    AFTER_HOURS,
    CHANNEL_MIX,
    DEVICE_AFTER_SWAP_SECONDS,
    FRAUDSTER_CITIES,
    FRAUDSTER_PLATFORM,
    MIN_LEFT_CENTS,
    NEW_PAYEES,
    PAYMENT_GAP_SECONDS,
    AccountTakeover,
    AccountTakeoverEpisode,
)
from sentinel.simulator.fraud.cash_out import Round
from sentinel.simulator.fraud.episodes import FraudActions, Victim
from sentinel.simulator.geography import CITIES
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import PlannedPayment
from sentinel.simulator.sessions import APP_LOCATION_JITTER_DEGREES, CHANNEL_BY_PLATFORM
from sentinel.simulator.unusual_events import EventIds

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)  # a Wednesday
RICH = 10**12  # far more than any daily limit
PAYOUT_CHANNELS = (Channel.PAYSHAP, Channel.EFT)


class AccountBalances:
    """Fixed amounts available, by account ID, and the same for every other account."""

    def __init__(self, default: int, available: dict[str, int] | None = None) -> None:
        self.default = default
        self.available = available or {}

    def available_cents(self, account_id: str) -> int:
        return self.available.get(account_id, self.default)


def event_ids() -> EventIds:
    return EventIds(
        sessions=IdSequence("SES"),
        devices=IdSequence("DEV"),
        beneficiaries=IdSequence("BEN"),
        beneficiary_events=IdSequence("BEV"),
        sim_swaps=IdSequence("SIM"),
    )


@pytest.fixture(scope="module")
def scenario() -> AccountTakeover:
    return AccountTakeover(event_ids(), IdSequence("TRF", digits=9))


@pytest.fixture(scope="module")
def episodes(scenario: AccountTakeover, victims: list[Victim]) -> list[AccountTakeoverEpisode]:
    rng = random.Random(3)
    planned = []
    for number in range(1, 401):
        victim = scenario.choose_victim(rng, victims, START)
        assert victim is not None
        planned.append(scenario.plan(rng, victim, START, f"SCN-{number:07d}"))
    return planned


def on(
    episodes: list[AccountTakeoverEpisode], channel: BankingChannel
) -> list[AccountTakeoverEpisode]:
    return [episode for episode in episodes if episode.channel is channel]


def play(
    episode: AccountTakeoverEpisode, balances: AccountBalances
) -> tuple[FraudActions, FraudActions]:
    """Both rounds, in order: the second needs what the first left behind."""
    first, second = (to_sast(spree.start).date() for spree in episode.rounds)
    return episode.act(first, balances), episode.act(second, balances)


def debits(actions: FraudActions, *channels: Channel) -> list[TransactionDraft]:
    payments = [a for a in actions.attempts if isinstance(a, PlannedPayment)]
    return [p.debit for p in payments if p.debit.channel in channels]


def transfers(actions: FraudActions) -> list[PlannedPayment]:
    payments = [a for a in actions.attempts if isinstance(a, PlannedPayment)]
    return [p for p in payments if p.debit.channel is Channel.INTERNAL_TRANSFER]


def total(drafts: list[TransactionDraft]) -> int:
    return sum(draft.amount_cents for draft in drafts)


def test_the_channel_is_drawn_first_and_the_victim_banks_on_it(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    counts = Counter(episode.channel for episode in episodes)
    for channel, share in CHANNEL_MIX.items():
        assert counts[channel] / len(episodes) == pytest.approx(share, abs=0.07)

    for episode in episodes:
        platforms = {item.device.platform for item in episode.victim.payments.devices}
        assert episode.channel in {CHANNEL_BY_PLATFORM[p] for p in platforms}
        assert episode.params["channel"] == episode.channel.value


def test_swaps_lean_to_the_weekend_and_the_evening(episodes: list[AccountTakeoverEpisode]) -> None:
    swaps = [to_sast(episode.swapped_at) for episode in episodes]
    weekend = sum(swap.weekday() >= 4 for swap in swaps) / len(swaps)
    evening = sum(swap.hour >= AFTER_HOURS[0] for swap in swaps) / len(swaps)

    assert 0.5 < weekend < 0.7  # Friday to Sunday carry 6 of the 10 weights
    assert 0.5 < evening < 0.75
    for swap in swaps:
        assert 0 <= (swap.date() - to_sast(START).date()).days < 7
        assert time(8) <= swap.time() < time(AFTER_HOURS[1])


def test_the_swap_is_on_the_victims_number_and_network(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes[:50]:
        first, _ = play(episode, AccountBalances(RICH))
        (swap,) = first.sim_swaps
        customer = episode.victim.customer
        assert (swap.customer_id, swap.phone_number) == (
            customer.customer_id,
            customer.phone_number,
        )
        assert (swap.swapped_at, swap.network) == (episode.swapped_at, episode.victim.network)


def test_a_device_is_registered_minutes_after_the_swap_like_any_other(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in [e for e in episodes if e.channel is not BankingChannel.USSD]:
        first, _ = play(episode, AccountBalances(RICH))
        (device,), (link,), (session,) = first.devices, first.device_links, first.sessions

        assert device.platform is FRAUDSTER_PLATFORM[episode.channel]
        assert link.link_method is LINK_METHOD_BY_PLATFORM[device.platform]
        assert (link.customer_id, link.device_id) == (episode.victim_customer_id, device.device_id)
        assert session.device_id == device.device_id
        assert session.started_at == link.linked_at == episode.rounds[0].start
        delay = (link.linked_at - episode.swapped_at).total_seconds()
        assert DEVICE_AFTER_SWAP_SECONDS[0] <= delay <= DEVICE_AFTER_SWAP_SECONDS[1]
        assert episode.params["login_after_minutes"] == int(delay // 60)


def test_app_logins_come_from_the_fraudsters_city(episodes: list[AccountTakeoverEpisode]) -> None:
    cities = {city.name: city for city in CITIES}
    for episode in on(episodes, BankingChannel.APP):
        city = cities[str(episode.params["fraudster_city"])]
        assert city.name in FRAUDSTER_CITIES
        assert city.name != episode.victim.customer.home_city

        first, second = play(episode, AccountBalances(RICH))
        for session in first.sessions + second.sessions:
            assert session.lat is not None and session.lon is not None
            assert abs(session.lat - city.lat) <= APP_LOCATION_JITTER_DEGREES + 0.0001
            assert abs(session.lon - city.lon) <= APP_LOCATION_JITTER_DEGREES + 0.0001
            assert session.ip_country == "ZA"
            assert not session.remote_access_detected


def test_ussd_takeovers_need_no_device(episodes: list[AccountTakeoverEpisode]) -> None:
    ussd = on(episodes, BankingChannel.USSD)

    assert ussd
    for episode in ussd:
        first, second = play(episode, AccountBalances(RICH))
        assert not first.devices
        assert not first.device_links
        for session in first.sessions + second.sessions:
            assert session.channel is BankingChannel.USSD
            assert (session.device_id, session.ip_country) == (None, None)
            assert session.auth_method is SessionAuthMethod.PIN
        payouts = debits(first, *PAYOUT_CHANNELS) + debits(second, *PAYOUT_CHANNELS)
        assert {d.auth_method for d in payouts} == {TransactionAuthMethod.PIN}


def test_round_one_is_swap_login_sweep_payees_then_payouts(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes[:100]:
        first, _ = play(episode, AccountBalances(RICH))
        (swap,), (session,) = first.sim_swaps, first.sessions
        sweep = [t.debit.event_time for t in transfers(first)]
        payees = first.beneficiaries
        payouts = debits(first, *PAYOUT_CHANNELS)

        times = [swap.swapped_at, session.started_at, *sweep]
        times += [p.created_at for p in payees] + [d.event_time for d in payouts]
        assert times == sorted(times)
        channels = [d.channel for d in payouts]
        assert channels == sorted(channels, key=PAYOUT_CHANNELS.index)  # PayShap first
        assert all(d.session_id == session.session_id for d in payouts)
        times_paid = [d.event_time for d in payouts]
        gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times_paid)]
        assert all(PAYMENT_GAP_SECONDS[0] <= gap <= PAYMENT_GAP_SECONDS[1] for gap in gaps)


def test_new_payees_are_saved_at_other_banks_in_the_fraudsters_session(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes[:100]:
        first, _ = play(episode, AccountBalances(RICH))
        payees, events = first.beneficiaries, first.beneficiary_events

        assert NEW_PAYEES[0] <= len(payees) <= NEW_PAYEES[1]
        assert len(payees) == episode.params["new_payees"]
        assert [e.beneficiary_id for e in events] == [p.beneficiary_id for p in payees]
        for payee, event in zip(payees, events, strict=True):
            assert payee.customer_id == episode.victim_customer_id
            assert payee.beneficiary_name in PERSONAL_PAYEE_NAMES
            assert payee.payee_bank in OTHER_BANKS
            assert payee.payee_internal_account_id is None
            assert event.event_type is BeneficiaryEventType.CREATED
            assert event.session_id == first.sessions[0].session_id
        paid = {d.beneficiary_id for d in debits(first, *PAYOUT_CHANNELS)}
        assert paid <= {p.beneficiary_id for p in payees}


def test_the_sweep_takes_the_savings_then_the_credit_card_within_their_limits(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    checked = 0
    for episode in episodes:
        habits = episode.victim.payments
        savings, credit, main = habits.savings_account, habits.credit_account, habits.main_account
        if savings is None or credit is None:
            continue
        balances = AccountBalances(RICH, {savings.account_id: 123_456})
        first, _ = play(episode, balances)
        sweep = transfers(first)

        legs = [(t.debit.account_id, t.credit.account_id if t.credit else None) for t in sweep]
        assert legs == [(savings.account_id, main.account_id), (credit.account_id, main.account_id)]
        assert sweep[0].debit.amount_cents == 123_400  # whole rands
        assert sweep[1].debit.amount_cents == credit.daily_transfer_limit_cents
        checked += 1
    assert checked


def test_nothing_is_swept_from_empty_accounts(episodes: list[AccountTakeoverEpisode]) -> None:
    for episode in episodes[:50]:
        main = episode.victim.payments.main_account
        first, _ = play(episode, AccountBalances(0, {main.account_id: RICH}))
        assert not transfers(first)


def test_payouts_fill_payshap_then_the_transfer_limit(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes[:100]:
        main = episode.victim.payments.main_account
        first, second = play(episode, AccountBalances(RICH))

        # Round one can be cut short at midnight; round two has the whole day.
        assert total(debits(first, Channel.PAYSHAP)) <= main.payshap_daily_limit_cents
        assert total(debits(first, Channel.EFT)) <= main.daily_transfer_limit_cents
        assert total(debits(second, Channel.PAYSHAP)) == main.payshap_daily_limit_cents
        assert total(debits(second, Channel.EFT)) == main.daily_transfer_limit_cents
        for channel in PAYOUT_CHANNELS:
            paid = Counter(d.beneficiary_id for d in debits(second, channel))
            assert paid == Counter(p.beneficiary_id for p in first.beneficiaries)


def test_payouts_stop_when_the_money_runs_out(episodes: list[AccountTakeoverEpisode]) -> None:
    for episode in episodes[:50]:
        main = episode.victim.payments.main_account
        first, second = play(episode, AccountBalances(0, {main.account_id: 1_000_050}))
        assert total(debits(first, Channel.PAYSHAP)) <= 1_000_000
        assert total(debits(second, Channel.PAYSHAP)) == 1_000_000  # whole rands
        assert not debits(first, Channel.EFT) + debits(second, Channel.EFT)

        overdrawn = play(episode, AccountBalances(0, {main.account_id: -500_000}))
        assert not [d for actions in overdrawn for d in debits(actions, *PAYOUT_CHANNELS)]


def test_the_second_round_comes_after_midnight_only_if_enough_is_left(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes[:50]:
        first_round, second_round = episode.rounds
        swap_day = to_sast(first_round.start).date()
        midnight = datetime.combine(swap_day + timedelta(days=1), time(), tzinfo=SAST)
        assert first_round.end == midnight < second_round.start
        assert to_sast(second_round.start).time() < time(0, 16)

        main = episode.victim.payments.main_account
        first, second = play(episode, AccountBalances(0, {main.account_id: MIN_LEFT_CENTS}))
        (session,) = second.sessions
        assert session.device_id == first.sessions[0].device_id
        assert not (second.sim_swaps or second.devices or second.beneficiaries)

        _, too_little = play(episode, AccountBalances(0, {main.account_id: MIN_LEFT_CENTS - 1}))
        assert too_little == FraudActions()


def test_the_second_round_needs_the_first(episodes: list[AccountTakeoverEpisode]) -> None:
    episode = replace(episodes[0], foothold=None)
    second_day = to_sast(episode.rounds[1].start).date()

    assert episode.act(second_day, AccountBalances(RICH)) == FraudActions()
    play(episode, AccountBalances(RICH))
    assert episode.act(second_day + timedelta(days=1), AccountBalances(RICH)) == FraudActions()


def test_payouts_never_run_past_the_rounds_end(episodes: list[AccountTakeoverEpisode]) -> None:
    episode = next(e for e in episodes if e.victim.payments.savings_account is not None)
    start = episode.rounds[0].start
    short = replace(episode, rounds=(Round(start, start + timedelta(seconds=1)), episode.rounds[1]))

    first, _ = play(short, AccountBalances(RICH))

    assert transfers(first)
    assert not debits(first, *PAYOUT_CHANNELS)


def test_the_episode_spans_two_days_and_uses_no_card(
    episodes: list[AccountTakeoverEpisode],
) -> None:
    for episode in episodes:
        swap_day = to_sast(episode.swapped_at).date()
        assert episode.days() == {swap_day, swap_day + timedelta(days=1)}
        assert episode.planned_end() == episode.rounds[1].end
        assert (episode.card_id, episode.card_loss) == (None, None)


def test_rounds_replay_the_same_way(episodes: list[AccountTakeoverEpisode]) -> None:
    def amounts(episode: AccountTakeoverEpisode) -> list[tuple[Channel, int, datetime]]:
        first, second = play(episode, AccountBalances(2_000_000))
        drafts = debits(first, *Channel) + debits(second, *Channel)
        return [(d.channel, d.amount_cents, d.event_time) for d in drafts]

    for episode in episodes[:30]:
        assert amounts(episode) == amounts(episode)


def test_nobody_is_chosen_without_a_device_for_the_channel(
    scenario: AccountTakeover, victims: list[Victim]
) -> None:
    victim = victims[0]
    offline = replace(victim, payments=replace(victim.payments, devices=()))

    assert scenario.choose_victim(random.Random(1), [offline], START) is None


def test_only_chosen_victims_can_be_planned(
    scenario: AccountTakeover, victims: list[Victim]
) -> None:
    with pytest.raises(SimulationError, match="not chosen"):
        scenario.plan(random.Random(1), victims[0], START, "SCN-0000001")
