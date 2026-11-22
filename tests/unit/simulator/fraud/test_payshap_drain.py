import itertools
import random
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import SAST, to_sast
from sentinel.domain.beneficiaries import BeneficiaryEventType
from sentinel.domain.devices import DevicePlatform
from sentinel.domain.labels import FraudType
from sentinel.domain.sessions import SessionAuthMethod
from sentinel.domain.transactions import Channel, TransactionAuthMethod
from sentinel.simulator.beneficiaries import OTHER_BANKS, PERSONAL_PAYEE_NAMES
from sentinel.simulator.devices import LINK_METHOD_BY_PLATFORM
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.account_takeover import FRAUDSTER_CITIES
from sentinel.simulator.fraud.episodes import FraudActions, Victim
from sentinel.simulator.fraud.payshap_drain import (
    ACTIVE_HOURS,
    NEW_PAYEES,
    PAYMENT_CENTS,
    PAYMENT_GAP_SECONDS,
    PayShapDrain,
    PayShapDrainEpisode,
)
from sentinel.simulator.geography import CITIES
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import PlannedPayment
from sentinel.simulator.sessions import APP_LOCATION_JITTER_DEGREES
from sentinel.simulator.unusual_events import EventIds

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)  # a Wednesday
RICH = 10**12  # far more than any daily limit


class AccountBalances:
    """Fixed amounts available, by account ID, and the same for every other account."""

    def __init__(self, default: int, available: dict[str, int] | None = None) -> None:
        self.default = default
        self.available = available or {}

    def available_cents(self, account_id: str) -> int:
        return self.available.get(account_id, self.default)


def scenario() -> PayShapDrain:
    ids = EventIds(
        sessions=IdSequence("SES"),
        devices=IdSequence("DEV"),
        beneficiaries=IdSequence("BEN"),
        beneficiary_events=IdSequence("BEV"),
        sim_swaps=IdSequence("SIM"),
    )
    return PayShapDrain(ids, IdSequence("TRF", digits=9))


@pytest.fixture(scope="module")
def episodes(victims: list[Victim]) -> list[PayShapDrainEpisode]:
    drains = scenario()
    rng = random.Random(5)
    planned = []
    for number in range(1, 201):
        victim = drains.choose_victim(rng, victims, START)
        assert victim is not None
        planned.append(drains.plan(rng, victim, START, f"SCN-{number:07d}"))
    return planned


def play(episode: PayShapDrainEpisode, balances: AccountBalances) -> FraudActions:
    return episode.act(to_sast(episode.logged_in_at).date(), balances)


def debits(actions: FraudActions, channel: Channel) -> list[TransactionDraft]:
    payments = [a for a in actions.attempts if isinstance(a, PlannedPayment)]
    return [p.debit for p in payments if p.debit.channel is channel]


def test_anyone_can_be_drained(victims: list[Victim]) -> None:
    drains = scenario()
    rng = random.Random(1)
    assert drains.choose_victim(rng, victims[:1], START) is victims[0]
    assert drains.choose_victim(rng, [], START) is None


def test_the_drain_is_planned_in_the_day_from_a_big_city_away_from_home(
    episodes: list[PayShapDrainEpisode],
) -> None:
    day = to_sast(START).date()
    midnight = datetime.combine(day + timedelta(days=1), time(), tzinfo=SAST)
    for episode in episodes:
        logged_in = to_sast(episode.logged_in_at)
        assert logged_in.date() == day
        assert ACTIVE_HOURS[0] <= logged_in.hour < ACTIVE_HOURS[1]
        assert episode.city.name in FRAUDSTER_CITIES
        assert episode.city.name != episode.victim.customer.home_city
        assert episode.params == {
            "fraudster_city": episode.city.name,
            "new_payees": episode.payee_count,
        }
        assert NEW_PAYEES[0] <= episode.payee_count <= NEW_PAYEES[1]
        assert episode.fraud_type is FraudType.PAYSHAP_DRAIN
        assert episode.days() == {day}
        assert episode.planned_end() == midnight
        assert episode.card_id is None
        assert episode.card_loss is None


def test_nothing_happens_on_other_days(episodes: list[PayShapDrainEpisode]) -> None:
    episode = episodes[0]
    next_day = to_sast(episode.logged_in_at).date() + timedelta(days=1)
    assert episode.act(next_day, AccountBalances(RICH)) == FraudActions()


def test_a_new_phone_is_linked_without_a_sim_swap_and_used_from_the_fraudsters_city(
    episodes: list[PayShapDrainEpisode],
) -> None:
    cities = {city.name: city for city in CITIES}
    for episode in episodes[::4]:
        actions = play(episode, AccountBalances(RICH))
        (device,), (link,), (session,) = actions.devices, actions.device_links, actions.sessions
        assert not actions.sim_swaps
        assert device.platform is DevicePlatform.ANDROID
        assert link.link_method is LINK_METHOD_BY_PLATFORM[DevicePlatform.ANDROID]
        assert link.customer_id == session.customer_id == episode.victim_customer_id
        assert link.device_id == session.device_id == device.device_id
        assert session.started_at == episode.logged_in_at
        city = cities[str(episode.params["fraudster_city"])]
        assert session.lat is not None
        assert session.lon is not None
        assert abs(session.lat - city.lat) <= APP_LOCATION_JITTER_DEGREES
        assert abs(session.lon - city.lon) <= APP_LOCATION_JITTER_DEGREES


def test_savings_and_credit_are_swept_into_main_first(
    episodes: list[PayShapDrainEpisode],
) -> None:
    checked = 0
    for episode in episodes:
        habits = episode.victim.payments
        savings, credit, main = habits.savings_account, habits.credit_account, habits.main_account
        if savings is None or credit is None:
            continue
        actions = play(episode, AccountBalances(RICH, {savings.account_id: 123_456}))
        sweep = debits(actions, Channel.INTERNAL_TRANSFER)
        assert [d.account_id for d in sweep] == [savings.account_id, credit.account_id]
        assert [d.amount_cents for d in sweep] == [123_400, credit.daily_transfer_limit_cents]
        first_payout = debits(actions, Channel.PAYSHAP)[0]
        assert sweep[-1].event_time < first_payout.event_time
        assert first_payout.account_id == main.account_id
        checked += 1
    assert checked


def test_payees_are_saved_by_shapid_in_the_fraudsters_session(
    episodes: list[PayShapDrainEpisode],
) -> None:
    for episode in episodes[::4]:
        actions = play(episode, AccountBalances(RICH))
        payees, events = actions.beneficiaries, actions.beneficiary_events
        assert len(payees) == episode.payee_count
        for payee, event in zip(payees, events, strict=True):
            assert payee.customer_id == episode.victim_customer_id
            assert payee.beneficiary_name in PERSONAL_PAYEE_NAMES
            assert payee.payee_bank in OTHER_BANKS
            assert payee.shap_id is not None
            assert event.event_type is BeneficiaryEventType.CREATED
            assert event.session_id == actions.sessions[0].session_id


def test_small_quick_payshap_payments_to_each_payee_in_turn_up_to_the_limit(
    episodes: list[PayShapDrainEpisode],
) -> None:
    smallest, largest = PAYMENT_CENTS
    for episode in episodes:
        main = episode.victim.payments.main_account
        actions = play(episode, AccountBalances(RICH))
        paid = debits(actions, Channel.PAYSHAP)
        payees = [p.beneficiary_id for p in actions.beneficiaries]

        assert sum(d.amount_cents for d in paid) == main.payshap_daily_limit_cents
        assert all(d.amount_cents <= largest and d.amount_cents % 100 == 0 for d in paid)
        assert all(d.amount_cents >= smallest for d in paid[:-1])  # the last takes what's left
        assert [d.beneficiary_id for d in paid] == list(
            itertools.islice(itertools.cycle(payees), len(paid))
        )
        gaps = [(b.event_time - a.event_time).total_seconds() for a, b in itertools.pairwise(paid)]
        assert all(PAYMENT_GAP_SECONDS[0] <= gap <= PAYMENT_GAP_SECONDS[1] for gap in gaps)
        if actions.sessions[0].auth_method is SessionAuthMethod.BIOMETRIC:
            assert all(d.auth_method is TransactionAuthMethod.BIOMETRIC for d in paid)


def test_the_drain_stops_when_the_money_runs_out(episodes: list[PayShapDrainEpisode]) -> None:
    for episode in episodes[::4]:
        main = episode.victim.payments.main_account
        actions = play(episode, AccountBalances(0, {main.account_id: 750_050}))
        assert not debits(actions, Channel.INTERNAL_TRANSFER)
        assert sum(d.amount_cents for d in debits(actions, Channel.PAYSHAP)) == 750_000


def test_an_empty_account_gets_the_phone_and_payees_but_no_payments(
    episodes: list[PayShapDrainEpisode],
) -> None:
    for episode in episodes[:20]:
        actions = play(episode, AccountBalances(0))
        assert actions.devices
        assert actions.beneficiaries
        assert not actions.attempts


def test_the_drain_stops_at_midnight(episodes: list[PayShapDrainEpisode]) -> None:
    episode = episodes[0]
    day = to_sast(episode.logged_in_at).date()
    late = datetime.combine(day, time(23, 50), tzinfo=SAST).astimezone(UTC)
    actions = play(replace(episode, logged_in_at=late), AccountBalances(RICH))
    paid = debits(actions, Channel.PAYSHAP)
    assert all(d.event_time < episode.planned_end() for d in paid)
    assert (
        sum(d.amount_cents for d in paid)
        < episode.victim.payments.main_account.payshap_daily_limit_cents
    )
