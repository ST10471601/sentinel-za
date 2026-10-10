"""Ground truth: a label for every transaction and a summary of every fraud episode."""

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sentinel.domain.labels import FraudType, ScenarioInstance, TransactionLabel
from sentinel.domain.transactions import Direction, Transaction, TransactionStatus
from sentinel.simulator.fraud.episodes import Episode
from sentinel.simulator.randomness import chance

SECONDS_PER_DAY = 86_400

# Days from the end of an episode until the bank hears about it (simulation assumptions).
REPORT_DELAY_DAYS: dict[FraudType, tuple[float, float]] = {
    FraudType.CARD_NOT_PRESENT: (2, 30),  # spotted on the statement, then a chargeback
    FraudType.CARD_PRESENT_LOST_STOLEN: (0, 1),  # the victim misses the card quickly
    FraudType.CARD_PRESENT_COUNTERFEIT: (3, 30),  # the victim still has the real card
    FraudType.SIM_SWAP_TAKEOVER: (0, 3),  # the phone suddenly has no signal
    FraudType.APP_VISHING: (0, 2),
    FraudType.APP_REMOTE_ACCESS: (0, 2),
    FraudType.SUPPLIER_MANDATE: (7, 45),  # found when the real supplier asks to be paid
    FraudType.PAYSHAP_DRAIN: (0, 2),
    FraudType.MULE_LAYERING: (7, 60),  # traced back from the victims' claims
}


@dataclass(slots=True)
class _Outcome:
    episode: Episode
    transactions: list[Transaction] = field(default_factory=list)


class GroundTruth:
    """Collects each episode's settled transactions and labels them when the run ends.

    Fraud labels wait for the end because reported_at depends on when the episode finished.
    """

    def __init__(self, rng: random.Random, unreported_share: float) -> None:
        self._rng = rng
        self._unreported_share = unreported_share
        self._outcomes: dict[str, _Outcome] = {}

    def add_episode(self, episode: Episode) -> None:
        """Start tracking a planned episode."""
        self._outcomes[episode.scenario_id] = _Outcome(episode)

    def record(self, scenario_id: str, transactions: Iterable[Transaction]) -> None:
        """Note the transactions an episode's attempt produced, approved or declined."""
        self._outcomes[scenario_id].transactions.extend(transactions)

    def finish(self) -> tuple[list[ScenarioInstance], list[TransactionLabel]]:
        """Summarise every episode that happened and label its transactions."""
        instances: list[ScenarioInstance] = []
        labels: list[TransactionLabel] = []
        for outcome in self._outcomes.values():
            if not outcome.transactions:
                continue  # planned to start after the run ended
            instance = _summarise(outcome)
            reported_at = self._reported_at(instance.fraud_type, instance.ended_at)
            instances.append(instance)
            labels += [
                TransactionLabel(
                    transaction_id=transaction.transaction_id,
                    is_fraud=True,
                    fraud_type=instance.fraud_type,
                    scenario_id=instance.scenario_id,
                    reported_at=reported_at,
                )
                for transaction in outcome.transactions
            ]
        return instances, labels

    def _reported_at(self, fraud_type: FraudType, ended_at: datetime) -> datetime | None:
        """When the bank hears about the episode, or None if it never does."""
        if chance(self._rng, self._unreported_share):
            return None
        low, high = REPORT_DELAY_DAYS[fraud_type]
        delay = round(self._rng.uniform(low, high) * SECONDS_PER_DAY)
        return ended_at + timedelta(seconds=delay)


def normal_labels(transactions: Iterable[Transaction]) -> list[TransactionLabel]:
    """Labels for transactions no fraudster touched."""
    return [
        TransactionLabel(
            transaction_id=transaction.transaction_id,
            is_fraud=False,
            fraud_type=None,
            scenario_id=None,
            reported_at=None,
        )
        for transaction in transactions
    ]


def _summarise(outcome: _Outcome) -> ScenarioInstance:
    episode, transactions = outcome.episode, outcome.transactions
    times = [transaction.event_time for transaction in transactions]
    # Only money that left an account counts as stolen; a transfer's credit leg is the same money.
    stolen = sum(
        t.amount_cents
        for t in transactions
        if t.direction is Direction.DEBIT and t.status is TransactionStatus.APPROVED
    )
    return ScenarioInstance(
        scenario_id=episode.scenario_id,
        fraud_type=episode.fraud_type,
        victim_customer_id=episode.victim_customer_id,
        started_at=min(times),
        ended_at=max(times),
        total_amount_cents=stolen,
        params=json.dumps(dict(episode.params), sort_keys=True),
    )
