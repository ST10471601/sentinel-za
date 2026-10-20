"""Ground truth: a label for every transaction and a summary of every fraud episode."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sentinel.domain.labels import ScenarioInstance, TransactionLabel
from sentinel.domain.transactions import Direction, Transaction, TransactionStatus
from sentinel.simulator.fraud.episodes import Episode


@dataclass(slots=True)
class _Outcome:
    episode: Episode
    reported_at: datetime | None
    transactions: list[Transaction] = field(default_factory=list)


class GroundTruth:
    """Collects each episode's settled transactions and labels them when the run ends."""

    def __init__(self) -> None:
        self._outcomes: dict[str, _Outcome] = {}

    def add_episode(self, episode: Episode, reported_at: datetime | None) -> None:
        """Start tracking a planned episode and when it will be reported."""
        self._outcomes[episode.scenario_id] = _Outcome(episode, reported_at)

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
            instances.append(instance)
            labels += [
                TransactionLabel(
                    transaction_id=transaction.transaction_id,
                    is_fraud=True,
                    fraud_type=instance.fraud_type,
                    scenario_id=instance.scenario_id,
                    reported_at=outcome.reported_at,
                )
                for transaction in outcome.transactions
            ]
        return instances, labels


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
