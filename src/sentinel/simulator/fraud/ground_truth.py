"""Ground truth: labels for transactions, enquiries, logins and SIM swaps, and episode summaries."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.labels import (
    BalanceEnquiryLabel,
    LoginSessionLabel,
    ScenarioInstance,
    SimSwapLabel,
    TransactionLabel,
)
from sentinel.domain.sessions import LoginSession
from sentinel.domain.sim_swaps import SimSwapEvent
from sentinel.domain.transactions import Channel, Direction, Transaction, TransactionStatus
from sentinel.simulator.fraud.episodes import Episode


@dataclass(slots=True)
class _Outcome:
    episode: Episode
    reported_at: datetime | None
    transactions: list[Transaction] = field(default_factory=list)
    enquiries: list[BalanceEnquiry] = field(default_factory=list)
    sessions: list[LoginSession] = field(default_factory=list)
    sim_swaps: list[SimSwapEvent] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True if the fraudster did nothing, e.g. the episode starts after the run ends."""
        return not (self.transactions or self.enquiries or self.sessions or self.sim_swaps)


@dataclass(frozen=True, slots=True)
class TruthRows:
    """Everything ground truth produces once the run ends."""

    scenarios: list[ScenarioInstance]
    transaction_labels: list[TransactionLabel]
    enquiry_labels: list[BalanceEnquiryLabel]
    session_labels: list[LoginSessionLabel]
    sim_swap_labels: list[SimSwapLabel]


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

    def record_sessions(self, scenario_id: str, sessions: Iterable[LoginSession]) -> None:
        """Note logins the fraudster made on the victim's profile."""
        self._outcomes[scenario_id].sessions.extend(sessions)

    def record_sim_swaps(self, scenario_id: str, sim_swaps: Iterable[SimSwapEvent]) -> None:
        """Note SIM swaps the fraudster had done on the victim's number."""
        self._outcomes[scenario_id].sim_swaps.extend(sim_swaps)

    def finish(self) -> TruthRows:
        """Summarise every episode that happened and label what its fraudster did."""
        rows = TruthRows([], [], [], [], [])
        for outcome in self._outcomes.values():
            if outcome.is_empty():
                continue
            instance = _summarise(outcome)
            rows.scenarios.append(instance)
            _label_fraud(outcome, instance, rows)
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


def normal_session_labels(sessions: Iterable[LoginSession]) -> list[LoginSessionLabel]:
    """Labels for logins the customer made."""
    return [
        LoginSessionLabel(
            session_id=session.session_id,
            is_fraud=False,
            fraud_type=None,
            scenario_id=None,
            reported_at=None,
        )
        for session in sessions
    ]


def normal_sim_swap_labels(sim_swaps: Iterable[SimSwapEvent]) -> list[SimSwapLabel]:
    """Labels for SIM swaps the customer asked for."""
    return [
        SimSwapLabel(
            sim_swap_id=swap.sim_swap_id,
            is_fraud=False,
            fraud_type=None,
            scenario_id=None,
            reported_at=None,
        )
        for swap in sim_swaps
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


def _label_fraud(outcome: _Outcome, instance: ScenarioInstance, rows: TruthRows) -> None:
    """Label everything the episode's fraudster did. One report covers the whole episode."""
    fraud_type, scenario_id = instance.fraud_type, instance.scenario_id
    reported_at = outcome.reported_at
    rows.transaction_labels.extend(
        TransactionLabel(
            transaction_id=transaction.transaction_id,
            is_fraud=True,
            fraud_type=fraud_type,
            scenario_id=scenario_id,
            reported_at=reported_at,
        )
        for transaction in outcome.transactions
    )
    rows.enquiry_labels.extend(
        BalanceEnquiryLabel(
            balance_enquiry_id=enquiry.balance_enquiry_id,
            is_fraud=True,
            fraud_type=fraud_type,
            scenario_id=scenario_id,
            reported_at=reported_at,
        )
        for enquiry in outcome.enquiries
    )
    rows.session_labels.extend(
        LoginSessionLabel(
            session_id=session.session_id,
            is_fraud=True,
            fraud_type=fraud_type,
            scenario_id=scenario_id,
            reported_at=reported_at,
        )
        for session in outcome.sessions
    )
    rows.sim_swap_labels.extend(
        SimSwapLabel(
            sim_swap_id=swap.sim_swap_id,
            is_fraud=True,
            fraud_type=fraud_type,
            scenario_id=scenario_id,
            reported_at=reported_at,
        )
        for swap in outcome.sim_swaps
    )


def _summarise(outcome: _Outcome) -> ScenarioInstance:
    episode, transactions = outcome.episode, outcome.transactions
    # A balance check, login or SIM swap can come before the first transaction.
    times = [t.event_time for t in transactions] + [e.event_time for e in outcome.enquiries]
    times += [s.started_at for s in outcome.sessions] + [s.swapped_at for s in outcome.sim_swaps]
    # Only money that left the victim's accounts counts as stolen. A sweep between the victim's
    # own accounts would otherwise count the same money on the way in and again on the way out.
    stolen = sum(
        t.amount_cents
        for t in transactions
        if t.direction is Direction.DEBIT
        and t.status is TransactionStatus.APPROVED
        and t.channel is not Channel.INTERNAL_TRANSFER
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
