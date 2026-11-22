"""Authorised push payment scams: the victim is talked into paying a "safe account".

A caller posing as the bank's fraud team says the victim's money is at risk and must be moved to
a safe account. In a vishing scam the victim does every step while on the phone; in a remote
access scam the victim installs screen-sharing software and the caller drives the banking. Either
way the victim logs in on their own device from their usual place, so the login looks normal.
SABRIC 2025 gives how common each is (FRAUD_MIX) but not the steps, so the shape below is the
usual pattern and a simulation assumption.
"""

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.customers import BankingChannel
from sentinel.domain.devices import Device
from sentinel.domain.labels import FraudType
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import Channel
from sentinel.simulator.beneficiaries import PERSONAL_PAYEE_NAMES
from sentinel.simulator.fraud.episodes import Balances, FraudActions, ParamValue, Victim
from sentinel.simulator.fraud.payouts import split, sweep_into_main, whole_rands
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import TRANSFER_STEP_CENTS, PlannedPayment, payee_payment
from sentinel.simulator.randomness import chance, random_datetime_between
from sentinel.simulator.sessions import CHANNEL_BY_PLATFORM, devices_linked_by, start_session
from sentinel.simulator.unusual_events import ADD_PAYEE_DELAY_SECONDS, EventIds, save_new_payee

# Simulation assumptions.
CALL_HOURS = (8, 20)  # SAST; a call from "the bank" is believable in working hours
NEW_PAYEES = (1, 2)  # the "safe accounts", at other banks
PAYMENTS = (1, 3)
VICTIM_SHARE = (0.5, 1.0)  # of what each account can move: a victim typing keeps some back
PAYMENT_GAP_SECONDS: dict[FraudType, tuple[int, int]] = {
    FraudType.APP_VISHING: (120, 600),  # the victim types each step as the caller reads it out
    FraudType.APP_REMOTE_ACCESS: (30, 120),  # the caller drives
}
BROWSER_DETECTION_SHARE = 0.5  # the app sees screen sharing; a browser misses most tools
ONLINE_CHANNELS = frozenset({BankingChannel.APP, BankingChannel.INTERNET})


@dataclass(frozen=True, slots=True)
class AppScamEpisode:
    """One phone call: the victim's own login, money swept into main, new payees, payouts."""

    scenario_id: str
    fraud_type: FraudType
    victim: Victim
    params: Mapping[str, ParamValue]
    device: Device  # the victim's own phone or browser
    logged_in_at: datetime
    remote_access_detected: bool
    payee_count: int
    payment_count: int
    ids: EventIds
    transfer_groups: IdSequence
    seed: int

    @property
    def victim_customer_id(self) -> str:
        """The customer who is talked into paying."""
        return self.victim.customer.customer_id

    @property
    def card_id(self) -> None:
        """No card is used, so none is blocked when the fraud is reported."""
        return None

    @property
    def card_loss(self) -> None:
        """The victim keeps every card."""
        return None

    def days(self) -> frozenset[date]:
        """The day of the call."""
        return frozenset({_sa_day(self.logged_in_at)})

    def planned_end(self) -> datetime:
        """Midnight after the call; the payouts are long done by then."""
        next_day = _sa_day(self.logged_in_at) + timedelta(days=1)
        return datetime.combine(next_day, time(), tzinfo=SAST).astimezone(UTC)

    def act(self, day: date, balances: Balances) -> FraudActions:
        """On the day of the call: log in, sweep into main, add the safe accounts and pay them."""
        if day != _sa_day(self.logged_in_at):
            return FraudActions()
        rng = random.Random(self.seed)
        victim = self.victim
        session = start_session(
            rng, self.ids.sessions, victim.customer, [self.device], self.logged_in_at
        )
        if self.remote_access_detected:
            session = session.model_copy(update={"remote_access_detected": True})
        transfers, budget = sweep_into_main(
            rng, self.transfer_groups, victim.payments, session, balances, self._mover(rng)
        )
        actions = FraudActions(sessions=[session])
        actions.attempts += transfers
        moment = transfers[-1].debit.event_time if transfers else session.started_at
        for _ in range(self.payee_count):
            moment += _seconds(rng, ADD_PAYEE_DELAY_SECONDS)
            name = rng.choice(PERSONAL_PAYEE_NAMES)
            payee, created = save_new_payee(rng, self.ids, victim.customer, name, session, moment)
            actions.beneficiaries.append(payee)
            actions.beneficiary_events.append(created)
        actions.attempts += self._pay_out(rng, session, moment, budget, actions.beneficiaries)
        return actions

    def _mover(self, rng: random.Random) -> Callable[[int], int]:
        """The caller moves everything; a victim typing moves part of it, in round amounts."""
        if self.fraud_type is FraudType.APP_REMOTE_ACCESS:
            return whole_rands

        def victims_part(usable_cents: int) -> int:
            part = int(usable_cents * rng.uniform(*VICTIM_SHARE))
            return part // TRANSFER_STEP_CENTS * TRANSFER_STEP_CENTS

        return victims_part

    def _pay_out(
        self,
        rng: random.Random,
        session: LoginSession,
        moment: datetime,
        budget: int,
        payees: Sequence[Beneficiary],
    ) -> list[PlannedPayment]:
        """PayShap when it fits, else the channel with more room; never past either daily limit."""
        main = self.victim.payments.main_account
        room = {
            Channel.PAYSHAP: main.payshap_daily_limit_cents,
            Channel.EFT: main.daily_transfer_limit_cents,
        }
        total = min(budget, sum(room.values()))
        gap = PAYMENT_GAP_SECONDS[self.fraud_type]
        payouts: list[PlannedPayment] = []
        for number, share in enumerate(split(rng, total, self.payment_count)):
            fits = share <= room[Channel.PAYSHAP]
            channel = (
                Channel.PAYSHAP
                if fits or room[Channel.PAYSHAP] >= room[Channel.EFT]
                else Channel.EFT
            )
            amount = min(share, room[channel])
            room[channel] -= amount
            moment += _seconds(rng, gap)
            payee = payees[number % len(payees)]
            payouts.append(payee_payment(main, session, payee, amount, moment, channel))
        return payouts


class AppScam:
    """Plans vishing or remote access scams. Their logins, payees and transfers use run IDs."""

    def __init__(self, fraud_type: FraudType, ids: EventIds, transfer_groups: IdSequence) -> None:
        if fraud_type not in PAYMENT_GAP_SECONDS:
            raise ValueError(f"{fraud_type.value} is not an app scam")
        self.fraud_type = fraud_type
        self._ids = ids
        self._transfer_groups = transfer_groups

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """Anyone who banks on a phone or in a browser; the balance doesn't matter."""
        day = _sa_day(start)
        targets = [victim for victim in candidates if _online_devices(victim, day)]
        return rng.choice(targets) if targets else None

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> AppScamEpisode:
        """Plan the call on the day of ``start``, on one of the victim's own devices."""
        day = _sa_day(start)
        devices = _online_devices(victim, day)
        if not devices:
            raise SimulationError(f"{victim.customer.customer_id} has no phone or browser linked")
        device = rng.choice(devices)
        channel = CHANNEL_BY_PLATFORM[device.platform]
        opens, closes = (datetime.combine(day, time(hour), tzinfo=SAST) for hour in CALL_HOURS)
        logged_in_at = random_datetime_between(rng, opens, closes).astimezone(UTC)
        detected = self.fraud_type is FraudType.APP_REMOTE_ACCESS and (
            channel is BankingChannel.APP or chance(rng, BROWSER_DETECTION_SHARE)
        )
        payee_count = rng.randint(*NEW_PAYEES)
        payment_count = rng.randint(*PAYMENTS)
        return AppScamEpisode(
            scenario_id=scenario_id,
            fraud_type=self.fraud_type,
            victim=victim,
            params={
                "channel": channel.value,
                "remote_access_detected": detected,
                "new_payees": payee_count,
                "payments": payment_count,
            },
            device=device,
            logged_in_at=logged_in_at,
            remote_access_detected=detected,
            payee_count=payee_count,
            payment_count=payment_count,
            ids=self._ids,
            transfer_groups=self._transfer_groups,
            seed=rng.getrandbits(32),
        )


def _online_devices(victim: Victim, day: date) -> list[Device]:
    """The victim's phones and browsers, linked before ``day`` starts."""
    day_starts = datetime.combine(day, time(), tzinfo=SAST)
    devices = devices_linked_by(victim.payments.devices, day_starts)
    return [d for d in devices if CHANNEL_BY_PLATFORM[d.platform] in ONLINE_CHANNELS]


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
