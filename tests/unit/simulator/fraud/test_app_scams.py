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
from sentinel.domain.labels import FraudType
from sentinel.domain.transactions import Channel
from sentinel.simulator.beneficiaries import OTHER_BANKS, PERSONAL_PAYEE_NAMES
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.app_scams import (
    BROWSER_DETECTION_SHARE,
    CALL_HOURS,
    NEW_PAYEES,
    PAYMENT_GAP_SECONDS,
    PAYMENTS,
    VICTIM_SHARE,
    AppScam,
    AppScamEpisode,
)
from sentinel.simulator.fraud.episodes import FraudActions, Victim
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import TRANSFER_STEP_CENTS, PlannedPayment
from sentinel.simulator.sessions import CHANNEL_BY_PLATFORM, HOME_COUNTRY
from sentinel.simulator.unusual_events import EventIds

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)  # a Wednesday
RICH = 10**12  # far more than any daily limit
PAYOUT_CHANNELS = (Channel.PAYSHAP, Channel.EFT)
ONLINE = {BankingChannel.APP, BankingChannel.INTERNET}


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


def scenario(fraud_type: FraudType) -> AppScam:
    return AppScam(fraud_type, event_ids(), IdSequence("TRF", digits=9))


def plan_many(fraud_type: FraudType, victims: list[Victim]) -> list[AppScamEpisode]:
    scams = scenario(fraud_type)
    rng = random.Random(4)
    planned = []
    for number in range(1, 301):
        victim = scams.choose_victim(rng, victims, START)
        assert victim is not None
        planned.append(scams.plan(rng, victim, START, f"SCN-{number:07d}"))
    return planned


@pytest.fixture(scope="module")
def vishing(victims: list[Victim]) -> list[AppScamEpisode]:
    return plan_many(FraudType.APP_VISHING, victims)


@pytest.fixture(scope="module")
def remote_access(victims: list[Victim]) -> list[AppScamEpisode]:
    return plan_many(FraudType.APP_REMOTE_ACCESS, victims)


@pytest.fixture(scope="module")
def episodes(
    vishing: list[AppScamEpisode], remote_access: list[AppScamEpisode]
) -> list[AppScamEpisode]:
    return vishing + remote_access


def play(episode: AppScamEpisode, balances: AccountBalances) -> FraudActions:
    return episode.act(to_sast(episode.logged_in_at).date(), balances)


def debits(actions: FraudActions, *channels: Channel) -> list[TransactionDraft]:
    payments = [a for a in actions.attempts if isinstance(a, PlannedPayment)]
    return [p.debit for p in payments if p.debit.channel in channels]


def test_only_vishing_and_remote_access_are_app_scams() -> None:
    with pytest.raises(ValueError, match="sim_swap_takeover"):
        scenario(FraudType.SIM_SWAP_TAKEOVER)


def test_victims_bank_on_a_phone_or_browser_they_already_had(
    episodes: list[AppScamEpisode],
) -> None:
    day_starts = datetime.combine(to_sast(START).date(), time(), tzinfo=SAST)
    for episode in episodes:
        linked = {i.device.device_id: i.linked_at for i in episode.victim.payments.devices}
        assert linked[episode.device.device_id] <= day_starts
        channel = CHANNEL_BY_PLATFORM[episode.device.platform]
        assert channel in ONLINE
        assert episode.params["channel"] == channel.value


def test_nobody_is_chosen_without_a_phone_or_browser_linked_before_the_day(
    victims: list[Victim],
) -> None:
    victim = victims[0]
    later = START + timedelta(days=1)
    linked_later = tuple(replace(item, linked_at=later) for item in victim.payments.devices)
    offline = [
        replace(victim, payments=replace(victim.payments, devices=())),
        replace(victim, payments=replace(victim.payments, devices=linked_later)),
    ]
    scams = scenario(FraudType.APP_VISHING)
    rng = random.Random(1)

    assert scams.choose_victim(rng, offline, START) is None
    with pytest.raises(SimulationError, match=victim.customer.customer_id):
        scams.plan(rng, offline[0], START, "SCN-0000001")


def test_the_call_comes_in_working_hours_on_the_start_day(
    episodes: list[AppScamEpisode],
) -> None:
    day = to_sast(START).date()
    midnight = datetime.combine(day + timedelta(days=1), time(), tzinfo=SAST)
    for episode in episodes:
        called = to_sast(episode.logged_in_at)
        assert called.date() == day
        assert CALL_HOURS[0] <= called.hour < CALL_HOURS[1]
        assert episode.days() == {day}
        assert episode.planned_end() == midnight
        assert episode.card_id is None
        assert episode.card_loss is None


def test_remote_access_is_always_seen_in_the_app_and_half_the_time_in_a_browser(
    vishing: list[AppScamEpisode], remote_access: list[AppScamEpisode]
) -> None:
    assert not any(episode.remote_access_detected for episode in vishing)
    in_browser = []
    for episode in remote_access:
        assert episode.params["remote_access_detected"] == episode.remote_access_detected
        if CHANNEL_BY_PLATFORM[episode.device.platform] is BankingChannel.APP:
            assert episode.remote_access_detected
        else:
            in_browser.append(episode.remote_access_detected)
    assert in_browser
    assert sum(in_browser) / len(in_browser) == pytest.approx(BROWSER_DETECTION_SHARE, abs=0.15)


def test_nothing_happens_on_other_days(episodes: list[AppScamEpisode]) -> None:
    episode = episodes[0]
    next_day = to_sast(episode.logged_in_at).date() + timedelta(days=1)
    assert episode.act(next_day, AccountBalances(RICH)) == FraudActions()


def test_one_login_on_the_victims_own_device_from_home(episodes: list[AppScamEpisode]) -> None:
    for episode in episodes[::5]:
        actions = play(episode, AccountBalances(RICH))
        (session,) = actions.sessions
        assert session.customer_id == episode.victim_customer_id
        assert session.device_id == episode.device.device_id
        assert session.started_at == episode.logged_in_at
        assert session.ip_country == HOME_COUNTRY
        assert session.remote_access_detected is episode.remote_access_detected
        assert not actions.devices
        assert not actions.sim_swaps
        assert not actions.genuine
        drafts = debits(actions, Channel.INTERNAL_TRANSFER, *PAYOUT_CHANNELS)
        assert all(d.session_id == session.session_id for d in drafts)


def test_sweep_then_payees_then_payouts_at_the_scams_pace(episodes: list[AppScamEpisode]) -> None:
    for episode in episodes[::3]:
        actions = play(episode, AccountBalances(RICH))
        paid = debits(actions, *PAYOUT_CHANNELS)
        times = [actions.sessions[0].started_at]
        times += [d.event_time for d in debits(actions, Channel.INTERNAL_TRANSFER)]
        times += [p.created_at for p in actions.beneficiaries] + [d.event_time for d in paid]
        assert times == sorted(times)
        low, high = PAYMENT_GAP_SECONDS[episode.fraud_type]
        gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(d.event_time for d in paid)]
        assert all(low <= gap <= high for gap in gaps)


def test_the_safe_accounts_are_new_payees_at_other_banks(episodes: list[AppScamEpisode]) -> None:
    for episode in episodes[::3]:
        actions = play(episode, AccountBalances(RICH))
        payees, events = actions.beneficiaries, actions.beneficiary_events
        assert NEW_PAYEES[0] <= len(payees) <= NEW_PAYEES[1]
        assert len(payees) == episode.params["new_payees"]
        for payee, event in zip(payees, events, strict=True):
            assert payee.customer_id == episode.victim_customer_id
            assert payee.beneficiary_name in PERSONAL_PAYEE_NAMES
            assert payee.payee_bank in OTHER_BANKS
            assert event.event_type is BeneficiaryEventType.CREATED
            assert event.session_id == actions.sessions[0].session_id
        paid = {d.beneficiary_id for d in debits(actions, *PAYOUT_CHANNELS)}
        assert paid <= {p.beneficiary_id for p in payees}


def test_a_victim_moves_part_of_each_account_and_the_caller_moves_it_all(
    episodes: list[AppScamEpisode],
) -> None:
    checked: Counter[FraudType] = Counter()
    for episode in episodes:
        habits = episode.victim.payments
        savings, credit = habits.savings_account, habits.credit_account
        if savings is None or credit is None:
            continue
        balances = AccountBalances(RICH, {savings.account_id: 123_456})
        sweep = debits(play(episode, balances), Channel.INTERNAL_TRANSFER)

        assert [d.account_id for d in sweep] == [savings.account_id, credit.account_id]
        usable = (123_456, credit.daily_transfer_limit_cents)
        for draft, most in zip(sweep, usable, strict=True):
            if episode.fraud_type is FraudType.APP_REMOTE_ACCESS:
                assert draft.amount_cents == most // 100 * 100
            else:
                assert most * VICTIM_SHARE[0] - TRANSFER_STEP_CENTS < draft.amount_cents <= most
                assert draft.amount_cents % TRANSFER_STEP_CENTS == 0
        checked[episode.fraud_type] += 1
    assert set(checked) == {FraudType.APP_VISHING, FraudType.APP_REMOTE_ACCESS}


def test_payouts_go_by_payshap_or_eft_within_both_daily_limits(
    episodes: list[AppScamEpisode],
) -> None:
    seen: set[Channel] = set()
    for episode in episodes:
        main = episode.victim.payments.main_account
        paid = debits(play(episode, AccountBalances(RICH)), *PAYOUT_CHANNELS)
        assert episode.params["payments"] == episode.payment_count
        assert 1 <= len(paid) <= episode.payment_count <= PAYMENTS[1]
        by_channel: Counter[Channel] = Counter()
        for draft in paid:
            assert draft.account_id == main.account_id
            assert draft.amount_cents % 100 == 0
            by_channel[draft.channel] += draft.amount_cents
        assert by_channel[Channel.PAYSHAP] <= main.payshap_daily_limit_cents
        assert by_channel[Channel.EFT] <= main.daily_transfer_limit_cents
        seen |= set(by_channel)
    assert seen == set(PAYOUT_CHANNELS)


def test_payouts_take_what_the_main_account_holds_in_whole_rands(
    episodes: list[AppScamEpisode],
) -> None:
    for episode in episodes[::4]:
        main = episode.victim.payments.main_account
        actions = play(episode, AccountBalances(0, {main.account_id: 250_050}))
        assert not debits(actions, Channel.INTERNAL_TRANSFER)
        assert sum(d.amount_cents for d in debits(actions, *PAYOUT_CHANNELS)) == 250_000


def test_an_empty_account_still_gets_the_login_and_payees(episodes: list[AppScamEpisode]) -> None:
    for episode in episodes[:20]:
        actions = play(episode, AccountBalances(0))
        assert actions.sessions
        assert actions.beneficiaries
        assert not actions.attempts
