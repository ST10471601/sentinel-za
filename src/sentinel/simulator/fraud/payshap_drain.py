"""PayShap drains: a phished one-time PIN lets a fraudster link a new phone and empty the account.

The victim is tricked into sharing their login details and a one-time PIN, for example by a fake
buyer or courier. The fraudster registers the banking app on their own phone, saves several payees
by ShapID and sends quick PayShap payments, each small enough to skip the OTP that would go to the
victim's phone. There is no SIM swap: the victim gets an SMS for every payment, so the fraudster
races and doesn't come back. SABRIC 2025 gives how common this is (FRAUD_MIX) but not the steps,
so the shape below is the usual pattern and a simulation assumption.
"""

import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.devices import DevicePlatform
from sentinel.domain.labels import FraudType
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import Channel
from sentinel.simulator.beneficiaries import PERSONAL_PAYEE_NAMES
from sentinel.simulator.fraud.account_takeover import fraudster_city
from sentinel.simulator.fraud.episodes import Balances, FraudActions, ParamValue, Victim
from sentinel.simulator.fraud.payouts import RAND_CENTS, sweep_into_main, whole_rands
from sentinel.simulator.geography import City
from sentinel.simulator.identifiers import IdSequence, make_phone_number
from sentinel.simulator.payments import OTP_THRESHOLD_CENTS, PlannedPayment, payee_payment
from sentinel.simulator.randomness import random_datetime_between
from sentinel.simulator.unusual_events import (
    ADD_PAYEE_DELAY_SECONDS,
    EventIds,
    away_customer,
    register_device,
    save_new_payee,
)

# Simulation assumptions.
ACTIVE_HOURS = (8, 21)  # SAST
FRAUDSTER_PLATFORM = DevicePlatform.ANDROID
NEW_PAYEES = (2, 5)
PAYMENT_CENTS = (50_000, OTP_THRESHOLD_CENTS)  # R500 up to R3,000: above it an OTP is sent
PAYMENT_GAP_SECONDS = (20, 90)


@dataclass(frozen=True, slots=True)
class PayShapDrainEpisode:
    """A new phone linked with a phished OTP, ShapID payees, then PayShap until it runs out."""

    scenario_id: str
    victim: Victim
    params: Mapping[str, ParamValue]
    logged_in_at: datetime
    city: City  # where the fraudster logs in from
    payee_count: int
    ids: EventIds
    transfer_groups: IdSequence
    seed: int

    fraud_type = FraudType.PAYSHAP_DRAIN

    @property
    def victim_customer_id(self) -> str:
        """The customer whose account is drained."""
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
        """The day of the drain."""
        return frozenset({_sa_day(self.logged_in_at)})

    def planned_end(self) -> datetime:
        """Midnight after the drain, when the PayShap limit resets."""
        return _midnight_after(self.logged_in_at)

    def act(self, day: date, balances: Balances) -> FraudActions:
        """Link the phone, sweep into main, save the ShapID payees and drain by PayShap."""
        if day != _sa_day(self.logged_in_at):
            return FraudActions()
        rng = random.Random(self.seed)
        victim = self.victim
        away = away_customer(victim.customer, self.city)
        change = register_device(rng, self.ids, away, FRAUDSTER_PLATFORM, self.logged_in_at)
        session = change.session
        actions = FraudActions(
            devices=[change.device], device_links=[change.link], sessions=[session]
        )
        transfers, budget = sweep_into_main(
            rng, self.transfer_groups, victim.payments, session, balances, whole_rands
        )
        actions.attempts += transfers
        moment = transfers[-1].debit.event_time if transfers else session.started_at
        for _ in range(self.payee_count):
            moment += _seconds(rng, ADD_PAYEE_DELAY_SECONDS)
            name = rng.choice(PERSONAL_PAYEE_NAMES)
            shap_id = make_phone_number(rng)
            payee, created = save_new_payee(
                rng, self.ids, victim.customer, name, session, moment, shap_id
            )
            actions.beneficiaries.append(payee)
            actions.beneficiary_events.append(created)
        actions.attempts += self._drain(rng, session, moment, budget, actions.beneficiaries)
        return actions

    def _drain(
        self,
        rng: random.Random,
        session: LoginSession,
        moment: datetime,
        budget: int,
        payees: Sequence[Beneficiary],
    ) -> list[PlannedPayment]:
        """Small PayShap payments, payee after payee, until the money or the limit runs out."""
        main = self.victim.payments.main_account
        left = whole_rands(min(budget, main.payshap_daily_limit_cents))
        smallest, largest = (cents // RAND_CENTS for cents in PAYMENT_CENTS)
        midnight = _midnight_after(self.logged_in_at)
        turns = itertools.cycle(payees)
        payouts: list[PlannedPayment] = []
        while True:
            amount = min(rng.randint(smallest, largest) * RAND_CENTS, left)
            moment += _seconds(rng, PAYMENT_GAP_SECONDS)
            if amount <= 0 or moment >= midnight:
                return payouts  # past midnight the limit resets, but the victim has seen the SMSes
            payee = next(turns)
            payouts.append(payee_payment(main, session, payee, amount, moment, Channel.PAYSHAP))
            left -= amount


class PayShapDrain:
    """Plans PayShap drains. Their logins, devices, payees and transfers use run IDs."""

    fraud_type = FraudType.PAYSHAP_DRAIN

    def __init__(self, ids: EventIds, transfer_groups: IdSequence) -> None:
        self._ids = ids
        self._transfer_groups = transfer_groups

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """Anyone: the fraudster registers the app, so the victim needn't bank on a phone."""
        return rng.choice(candidates) if candidates else None

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> PayShapDrainEpisode:
        """Plan the drain on the day of ``start``, from a big city away from the victim's home."""
        day = _sa_day(start)
        opens, closes = (datetime.combine(day, time(hour), tzinfo=SAST) for hour in ACTIVE_HOURS)
        logged_in_at = random_datetime_between(rng, opens, closes).astimezone(UTC)
        city = fraudster_city(rng, victim.customer.home_city)
        payee_count = rng.randint(*NEW_PAYEES)
        return PayShapDrainEpisode(
            scenario_id=scenario_id,
            victim=victim,
            params={"fraudster_city": city.name, "new_payees": payee_count},
            logged_in_at=logged_in_at,
            city=city,
            payee_count=payee_count,
            ids=self._ids,
            transfer_groups=self._transfer_groups,
            seed=rng.getrandbits(32),
        )


def _midnight_after(moment: datetime) -> datetime:
    next_day = _sa_day(moment) + timedelta(days=1)
    return datetime.combine(next_day, time(), tzinfo=SAST).astimezone(UTC)


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
