"""Ground truth: a label for every transaction and enquiry, and a summary of every episode."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.labels import BalanceEnquiryLabel, ScenarioInstance, TransactionLabel
from sentinel.domain.transactions import Direction, Transaction, TransactionStatus
from sentinel.simulator.fraud.episodes import Episode


@dataclass(slots=True)
class _Outcome:
    episode: Episode
    reported_at: datetime | None
    transactions: list[Transaction] = field(default_factory=list)
    enquiries: list[BalanceEnquiry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TruthRows:
    """Everything ground truth produces once the run ends."""

    scenarios: list[ScenarioInstance]
    transaction_labels: list[TransactionLabel]
    enquiry_labels: list[BalanceEnquiryLabel]


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

    def record_enquiry(self, scenario_id: str, enquiry: BalanceEnquiry) -> None:
        """Note a balance check the fraudster made."""
        self._outcomes[scenario_id].enquiries.append(enquiry)

    def finish(self) -> TruthRows:
        """Summarise every episode that happened and label what its fraudster did."""
        rows = TruthRows([], [], [])
        for outcome in self._outcomes.values():
            if not outcome.transactions and not outcome.enquiries:
                continue  # planned to start after the run ended
            instance = _summarise(outcome)
            fraud_type, scenario_id = instance.fraud_type, instance.scenario_id
            rows.scenarios.append(instance)
            rows.transaction_labels.extend(
                TransactionLabel(
                    transaction_id=transaction.transaction_id,
                    is_fraud=True,
                    fraud_type=fraud_type,
                    scenario_id=scenario_id,
                    reported_at=outcome.reported_at,
                )
                for transaction in outcome.transactions
            )
            rows.enquiry_labels.extend(
                BalanceEnquiryLabel(
                    balance_enquiry_id=enquiry.balance_enquiry_id,
                    is_fraud=True,
                    fraud_type=fraud_type,
                    scenario_id=scenario_id,
                    reported_at=outcome.reported_at,
                )
                for enquiry in outcome.enquiries
            )
        return rows


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


def normal_enquiry_label(enquiry: BalanceEnquiry) -> BalanceEnquiryLabel:
    """The label for a balance check the cardholder made."""
    return BalanceEnquiryLabel(
        balance_enquiry_id=enquiry.balance_enquiry_id,
        is_fraud=False,
        fraud_type=None,
        scenario_id=None,
        reported_at=None,
    )


def _summarise(outcome: _Outcome) -> ScenarioInstance:
    episode, transactions = outcome.episode, outcome.transactions
    # A fraudster's balance check can come before the first transaction.
    times = [t.event_time for t in transactions] + [e.event_time for e in outcome.enquiries]
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
