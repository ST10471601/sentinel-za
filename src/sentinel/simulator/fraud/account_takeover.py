"""SIM swap account takeovers: the victim's number moved to a fraudster's SIM, then banking emptied.

The victim's login details were phished beforehand, which the bank never sees. Once the number is
on the fraudster's SIM, the one-time PINs that protect new devices, payees and payments go to the
fraudster. SABRIC 2025 gives the average claim by channel (app R17,400, internet R73,600, USSD
R7,000) but not the steps, so the shape below is the usual pattern and a simulation assumption.
"""

import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.customers import BankingChannel, Customer
from sentinel.domain.devices import Device, DevicePlatform
from sentinel.domain.labels import FraudType
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import Channel
from sentinel.simulator.beneficiaries import PERSONAL_PAYEE_NAMES
from sentinel.simulator.fraud.cash_out import Round
from sentinel.simulator.fraud.episodes import Balances, FraudActions, ParamValue, Victim
from sentinel.simulator.fraud.lost_stolen import MIDNIGHT_ROUND_SECONDS
from sentinel.simulator.geography import CITIES, City
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import (
    PAYMENT_DELAY_SECONDS,
    PlannedPayment,
    own_transfer,
    payee_payment,
)
from sentinel.simulator.randomness import chance, pick, random_datetime_between
from sentinel.simulator.sessions import CHANNEL_BY_PLATFORM, devices_linked_by, start_session
from sentinel.simulator.unusual_events import (
    ADD_PAYEE_DELAY_SECONDS,
    STORE_HOURS,
    EventIds,
    away_customer,
    notify_sim_swap,
    register_device,
    save_new_payee,
)

# Simulation assumption. SABRIC 2025 splits the claim value, not the number of cases, by channel:
# app 70.5%, internet 28.6%, USSD 0.9%.
CHANNEL_MIX: dict[BankingChannel, float] = {
    BankingChannel.APP: 0.65,
    BankingChannel.INTERNET: 0.25,
    BankingChannel.USSD: 0.10,
}

# Simulation assumptions from here on.
FRAUDSTER_PLATFORM: dict[BankingChannel, DevicePlatform] = {
    BankingChannel.APP: DevicePlatform.ANDROID,
    BankingChannel.INTERNET: DevicePlatform.WEB,
}  # USSD needs no device: the swapped SIM is enough
SWAP_DAY_WEIGHTS = (1, 1, 1, 1, 2, 2, 2)  # Monday first; weekends, when victims react slower
AFTER_HOURS_SWAP_SHARE = 0.6  # the rest in network store hours
AFTER_HOURS = (17, 23)
DEVICE_AFTER_SWAP_SECONDS = (5 * 60, 30 * 60)
FRAUDSTER_CITIES = frozenset({"Johannesburg", "Pretoria", "Durban", "Cape Town"})
NEW_PAYEES = (1, 3)
PAYMENT_GAP_SECONDS = (60, 300)
MIN_LEFT_CENTS = 50_000  # R500; less isn't worth logging in again for
RAND_CENTS = 100  # the fraudster types whole rands


@dataclass(frozen=True, slots=True)
class Foothold:
    """How the fraudster gets back in: the device to log in on, and the payees already added."""

    device: Device
    customer: Customer  # as the bank sees the fraudster's logins, near the fraudster's city
    payees: tuple[Beneficiary, ...]


@dataclass(eq=False, slots=True)
class AccountTakeoverEpisode:
    """A SIM swap, a takeover of the victim's banking and payouts, then more after midnight."""

    scenario_id: str
    victim: Victim
    params: Mapping[str, ParamValue]
    channel: BankingChannel
    swapped_at: datetime
    city: City  # where the fraudster logs in from
    payee_count: int
    rounds: tuple[Round, Round]  # from the first login to midnight, then the next day
    ids: EventIds
    transfer_groups: IdSequence
    seed: int  # each round draws from its own stream, so the order days are played can't matter
    foothold: Foothold | None = None  # set by the first round, used by the second

    fraud_type = FraudType.SIM_SWAP_TAKEOVER

    @property
    def victim_customer_id(self) -> str:
        """The customer whose banking is taken over."""
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
        """The swap day, and the next day for the second round."""
        return frozenset(_sa_day(spree.start) for spree in self.rounds)

    def planned_end(self) -> datetime:
        """Midnight after the second round."""
        return self.rounds[1].end

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The swap, takeover and first payouts; after midnight, more if enough is left."""
        first, second = self.rounds
        if day == _sa_day(first.start):
            return self._take_over(first, balances)
        foothold = self.foothold
        if day != _sa_day(second.start) or foothold is None:
            return FraudActions()
        if self._money_left(balances) < MIN_LEFT_CENTS:
            return FraudActions()
        return self._come_back(second, balances, foothold)

    def _take_over(self, spree: Round, balances: Balances) -> FraudActions:
        """Swap the SIM, get in, sweep the money into the main account, add payees and pay out."""
        rng = self._rng(spree)
        victim = self.victim
        swap = notify_sim_swap(
            rng, self.ids.sim_swaps, victim.customer, victim.network, self.swapped_at
        )
        actions = FraudActions(sim_swaps=[swap])
        device, customer, session = self._first_login(rng, spree.start, actions)
        transfers, budget = self._sweep(rng, session, balances)
        moment = _last_time(transfers, session.started_at)
        for _ in range(self.payee_count):
            moment = _after(rng, moment, ADD_PAYEE_DELAY_SECONDS)
            name = rng.choice(PERSONAL_PAYEE_NAMES)
            payee, created = save_new_payee(rng, self.ids, victim.customer, name, session, moment)
            actions.beneficiaries.append(payee)
            actions.beneficiary_events.append(created)

        payees = tuple(actions.beneficiaries)
        self.foothold = Foothold(device, customer, payees)
        actions.sessions.append(session)
        actions.attempts += transfers
        actions.attempts += self._pay_out(rng, spree, session, moment, budget, payees)
        return actions

    def _first_login(
        self, rng: random.Random, logged_in_at: datetime, actions: FraudActions
    ) -> tuple[Device, Customer, LoginSession]:
        """Register the fraudster's phone or browser, or dial USSD from the swapped SIM."""
        customer = self.victim.customer
        if self.channel is BankingChannel.USSD:
            phone = _feature_phone(self.victim)
            session = start_session(rng, self.ids.sessions, customer, [phone], logged_in_at)
            return phone, customer, session
        away = away_customer(customer, self.city)
        platform = FRAUDSTER_PLATFORM[self.channel]
        change = register_device(rng, self.ids, away, platform, logged_in_at)
        actions.devices.append(change.device)
        actions.device_links.append(change.link)
        return change.device, away, change.session

    def _come_back(self, spree: Round, balances: Balances, foothold: Foothold) -> FraudActions:
        """Log in again once the daily limits reset, sweep again and pay the same payees."""
        rng = self._rng(spree)
        session = start_session(
            rng, self.ids.sessions, foothold.customer, [foothold.device], spree.start
        )
        transfers, budget = self._sweep(rng, session, balances)
        moment = _last_time(transfers, session.started_at)
        payouts = self._pay_out(rng, spree, session, moment, budget, foothold.payees)
        return FraudActions(attempts=[*transfers, *payouts], sessions=[session])

    def _sweep(
        self, rng: random.Random, session: LoginSession, balances: Balances
    ) -> tuple[list[PlannedPayment], int]:
        """Move the savings, then what the credit card has left, into the main account.

        Returns the transfers and how much the main account will then hold.
        """
        habits = self.victim.payments
        main = habits.main_account
        budget = balances.available_cents(main.account_id)
        moment = session.started_at
        transfers: list[PlannedPayment] = []
        for source in (habits.savings_account, habits.credit_account):
            if source is None:
                continue
            available = balances.available_cents(source.account_id)
            amount = _whole_rands(min(available, source.daily_transfer_limit_cents))
            if amount <= 0:
                continue
            moment = _after(rng, moment, PAYMENT_DELAY_SECONDS)
            transfers.append(
                own_transfer(self.transfer_groups, session, source, main, amount, moment)
            )
            budget += amount
        return transfers, budget

    def _pay_out(
        self,
        rng: random.Random,
        spree: Round,
        session: LoginSession,
        moment: datetime,
        budget: int,
        payees: Sequence[Beneficiary],
    ) -> list[PlannedPayment]:
        """PayShap up to its daily limit, then EFT up to the transfer limit, split among payees."""
        main = self.victim.payments.main_account
        payshap = min(budget, main.payshap_daily_limit_cents)
        eft = min(budget - payshap, main.daily_transfer_limit_cents)
        payouts: list[PlannedPayment] = []
        for channel, total in ((Channel.PAYSHAP, payshap), (Channel.EFT, eft)):
            for payee, amount in zip(payees, _split(rng, total, len(payees)), strict=False):
                moment = _after(rng, moment, PAYMENT_GAP_SECONDS)
                if moment >= spree.end:
                    return payouts  # the rest waits until the limits reset
                payouts.append(payee_payment(main, session, payee, amount, moment, channel))
        return payouts

    def _money_left(self, balances: Balances) -> int:
        """What the fraudster saw left behind in the victim's accounts."""
        habits = self.victim.payments
        accounts = (habits.main_account, habits.savings_account, habits.credit_account)
        return sum(
            max(balances.available_cents(account.account_id), 0)
            for account in accounts
            if account is not None
        )

    def _rng(self, spree: Round) -> random.Random:
        return random.Random(f"{self.seed}:{spree.start.isoformat()}")


class AccountTakeover:
    """Plans SIM swap account takeovers. Their logins, devices, payees and transfers use run IDs."""

    fraud_type = FraudType.SIM_SWAP_TAKEOVER

    def __init__(self, ids: EventIds, transfer_groups: IdSequence) -> None:
        self._ids = ids
        self._transfer_groups = transfer_groups
        self._channels: dict[str, BankingChannel] = {}  # chosen victims' channels, by customer ID

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """A channel from CHANNEL_MIX, then anyone who banks on it; the balance doesn't matter."""
        channel = pick(rng, CHANNEL_MIX)
        targets = [victim for victim in candidates if _banks_on(victim, channel, start)]
        if not targets:
            return None
        victim = rng.choice(targets)
        self._channels[victim.customer.customer_id] = channel
        return victim

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> AccountTakeoverEpisode:
        """Plan the swap in the week from ``start``, the first login and the midnight round."""
        customer = victim.customer
        channel = self._channels.pop(customer.customer_id, None)
        if channel is None:
            raise SimulationError(f"{customer.customer_id} was not chosen for an account takeover")
        swapped_at = _swap_time(rng, _sa_day(start))
        logged_in_at = swapped_at + _seconds(rng, DEVICE_AFTER_SWAP_SECONDS)
        midnight = _midnight_after(logged_in_at)
        comeback = midnight + _seconds(rng, MIDNIGHT_ROUND_SECONDS)
        city = _fraudster_city(rng, customer.home_city)
        payee_count = rng.randint(*NEW_PAYEES)
        return AccountTakeoverEpisode(
            scenario_id=scenario_id,
            victim=victim,
            params={
                "channel": channel.value,
                "fraudster_city": city.name,
                "login_after_minutes": int((logged_in_at - swapped_at).total_seconds() // 60),
                "new_payees": payee_count,
            },
            channel=channel,
            swapped_at=swapped_at,
            city=city,
            payee_count=payee_count,
            rounds=(Round(logged_in_at, midnight), Round(comeback, _midnight_after(comeback))),
            ids=self._ids,
            transfer_groups=self._transfer_groups,
            seed=rng.getrandbits(32),
        )


def _banks_on(victim: Victim, channel: BankingChannel, moment: datetime) -> bool:
    """Whether the victim has a device for ``channel`` on their profile at ``moment``."""
    devices = devices_linked_by(victim.payments.devices, moment)
    return any(CHANNEL_BY_PLATFORM[device.platform] is channel for device in devices)


def _feature_phone(victim: Victim) -> Device:
    """The phone a USSD victim banks on; the fraudster's SIM now answers for it."""
    return next(
        item.device
        for item in victim.payments.devices
        if item.device.platform is DevicePlatform.FEATURE_PHONE
    )


def _swap_time(rng: random.Random, first_day: date) -> datetime:
    """A day in the week from ``first_day``, leaning to the weekend, mostly after store hours."""
    week = [first_day + timedelta(days=n) for n in range(7)]
    day = pick(rng, {d: SWAP_DAY_WEIGHTS[d.weekday()] for d in week})
    is_after_hours = chance(rng, AFTER_HOURS_SWAP_SHARE)
    opens, closes = AFTER_HOURS if is_after_hours else STORE_HOURS[day.weekday()]
    start = datetime.combine(day, time(opens), tzinfo=SAST)
    end = start + timedelta(hours=closes - opens)
    return random_datetime_between(rng, start, end).astimezone(UTC)


def _fraudster_city(rng: random.Random, home_city: str) -> City:
    """A big city where the fraudster goes unnoticed, never the victim's own."""
    cities = [c for c in CITIES if c.name in FRAUDSTER_CITIES and c.name != home_city]
    return pick(rng, {city: city.weight for city in cities})


def _split(rng: random.Random, total_cents: int, parts: int) -> list[int]:
    """Random whole-rand shares of ``total_cents``, at most ``parts`` of them, none empty."""
    rands = total_cents // RAND_CENTS
    parts = min(parts, rands)
    if parts < 1:
        return []
    cuts = sorted(rng.sample(range(1, rands), parts - 1))
    return [(end - start) * RAND_CENTS for start, end in itertools.pairwise([0, *cuts, rands])]


def _whole_rands(cents: int) -> int:
    return cents // RAND_CENTS * RAND_CENTS


def _last_time(transfers: Sequence[PlannedPayment], default: datetime) -> datetime:
    return transfers[-1].debit.event_time if transfers else default


def _midnight_after(moment: datetime) -> datetime:
    midnight = datetime.combine(_sa_day(moment) + timedelta(days=1), time(), tzinfo=SAST)
    return midnight.astimezone(UTC)


def _after(rng: random.Random, moment: datetime, bounds: tuple[int, int]) -> datetime:
    return moment + _seconds(rng, bounds)


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
