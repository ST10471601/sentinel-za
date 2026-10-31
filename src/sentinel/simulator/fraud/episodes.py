"""The common shape of every fraud scenario, and of one fraud episode against a victim."""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Card
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent
from sentinel.domain.customers import Customer
from sentinel.domain.devices import CustomerDevice, Device
from sentinel.domain.labels import FraudType
from sentinel.domain.merchants import Merchant
from sentinel.domain.sessions import LoginSession
from sentinel.domain.sim_swaps import SimSwapEvent
from sentinel.simulator.drafts import EnquiryDraft, TransactionDraft
from sentinel.simulator.payments import PaymentHabits, PlannedPayment

type ParamValue = str | int | float | bool
type FraudAction = TransactionDraft | PlannedPayment | EnquiryDraft


@dataclass(frozen=True, slots=True)
class Victim:
    """What a fraudster can get at: the customer's cards, accounts, payees, devices and ATMs."""

    customer: Customer
    cards: tuple[Card, ...]
    payments: PaymentHabits
    favourite_atms: tuple[Merchant, ...]


@dataclass(slots=True)
class FraudActions:
    """What a fraudster does on one day, plus any of the victim's own transactions it needs.

    Identity events (SIM swaps, devices, logins, payees) are all the fraudster's.
    """

    attempts: list[FraudAction] = field(default_factory=list)
    genuine: list[TransactionDraft] = field(default_factory=list)  # labelled not fraud
    sim_swaps: list[SimSwapEvent] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    device_links: list[CustomerDevice] = field(default_factory=list)
    sessions: list[LoginSession] = field(default_factory=list)
    beneficiaries: list[Beneficiary] = field(default_factory=list)
    beneficiary_events: list[BeneficiaryEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CardLoss:
    """A physical card taken from the victim, and when the victim calls the bank about it."""

    card_id: str
    lost_at: datetime  # the owner can't use the card from here
    reported_lost_at: datetime  # the call blocks the card, even if no fraud is ever claimed


class Balances(Protocol):
    """Read access to the bank's balances, for fraud that drains what is there."""

    def available_cents(self, account_id: str) -> int:
        """How much can be spent from the account right now."""


class Episode(Protocol):
    """One fraud episode against one victim, from first to last fraudulent transaction."""

    @property
    def scenario_id(self) -> str:
        """The episode's ID, e.g. ``SCN-0000001``."""

    @property
    def fraud_type(self) -> FraudType:
        """The kind of fraud."""

    @property
    def victim_customer_id(self) -> str | None:
        """The customer being defrauded. Mule layering has none."""

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """The choices made when planning, stored for reproducibility."""

    @property
    def card_id(self) -> str | None:
        """The card the fraud uses, blocked when the fraud is reported. None if no card."""

    @property
    def card_loss(self) -> CardLoss | None:
        """The lost or stolen card, if the victim no longer has it."""

    def days(self) -> frozenset[date]:
        """SA calendar days on which the fraudster acts."""

    def planned_end(self) -> datetime:
        """When the fraudster plans to stop. Report delays count from here."""

    def act(self, day: date, balances: Balances) -> FraudActions:
        """What the fraudster does on ``day``."""


class Scenario(Protocol):
    """Plans episodes of one kind of fraud. Each scenario is one class with this interface."""

    @property
    def fraud_type(self) -> FraudType:
        """The kind of fraud this scenario injects."""

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """Pick the victim of an episode starting at ``start``, or None if nobody fits."""

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> Episode:
        """Plan one episode against ``victim`` that starts at ``start``."""


@dataclass(frozen=True, slots=True)
class ScriptedEpisode:
    """An episode whose every transaction is known when it is planned, e.g. a card burst."""

    scenario_id: str
    fraud_type: FraudType
    victim_customer_id: str
    params: Mapping[str, ParamValue]
    drafts: tuple[TransactionDraft, ...]
    card_id: str | None = None

    def __post_init__(self) -> None:
        if not self.drafts:
            raise ValueError("a scripted episode needs at least one draft")

    @property
    def card_loss(self) -> None:
        """Scripted fraud copies card details; the victim keeps the card."""
        return None

    def days(self) -> frozenset[date]:
        """SA calendar days on which the fraudster acts."""
        return frozenset(_sa_day(draft.event_time) for draft in self.drafts)

    def planned_end(self) -> datetime:
        """The time of the last scripted transaction."""
        return max(draft.event_time for draft in self.drafts)

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The drafts that fall on ``day``. Balances don't matter: the script is fixed."""
        return FraudActions([draft for draft in self.drafts if _sa_day(draft.event_time) == day])


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
