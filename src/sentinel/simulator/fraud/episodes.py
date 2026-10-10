"""The common shape of every fraud scenario, and of one fraud episode against a victim."""

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Card
from sentinel.domain.customers import Customer
from sentinel.domain.labels import FraudType
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.payments import PaymentHabits, PlannedPayment

type ParamValue = str | int | float | bool
type FraudAction = TransactionDraft | PlannedPayment


@dataclass(frozen=True, slots=True)
class Victim:
    """What a fraudster can get at: the customer's cards, accounts, payees and devices."""

    customer: Customer
    cards: tuple[Card, ...]
    payments: PaymentHabits


@dataclass(slots=True)
class FraudActions:
    """What a fraudster does on one day."""

    attempts: list[FraudAction] = field(default_factory=list)


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

    def days(self) -> frozenset[date]:
        """SA calendar days on which the fraudster acts."""

    def act(self, day: date, balances: Balances) -> FraudActions:
        """What the fraudster does on ``day``."""


class Scenario(Protocol):
    """Plans episodes of one kind of fraud. Each scenario is one class with this interface."""

    @property
    def fraud_type(self) -> FraudType:
        """The kind of fraud this scenario injects."""

    def can_target(self, victim: Victim) -> bool:
        """Whether this customer has what the fraud needs, e.g. a credit card."""

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

    def days(self) -> frozenset[date]:
        """SA calendar days on which the fraudster acts."""
        return frozenset(_sa_day(draft.event_time) for draft in self.drafts)

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The drafts that fall on ``day``. Balances don't matter: the script is fixed."""
        return FraudActions([draft for draft in self.drafts if _sa_day(draft.event_time) == day])


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
