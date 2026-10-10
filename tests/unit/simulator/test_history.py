from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import AccountType
from sentinel.domain.base import DomainModel
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.devices import Device
from sentinel.domain.labels import FraudType, ScenarioInstance, TransactionLabel
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import Channel, Direction, Transaction, TransactionStatus
from sentinel.simulator.fraud.planning import FraudSettings
from sentinel.simulator.history import HistorySummary, HistoryTable, simulate_history
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data

SEED = 21
CUSTOMERS = 150
MONTHS = 1


class CollectedRows:
    """A sink that keeps every row in memory, by table."""

    def __init__(self) -> None:
        self.rows: dict[HistoryTable, list[DomainModel]] = defaultdict(list)

    def add(self, table: HistoryTable, rows: Sequence[DomainModel]) -> None:
        self.rows[table].extend(rows)

    def transactions(self) -> list[Transaction]:
        return [row for row in self.rows[HistoryTable.TRANSACTION] if isinstance(row, Transaction)]

    def labels(self) -> list[TransactionLabel]:
        rows = self.rows[HistoryTable.TRANSACTION_LABEL]
        return [row for row in rows if isinstance(row, TransactionLabel)]

    def scenarios(self) -> list[ScenarioInstance]:
        rows = self.rows[HistoryTable.SCENARIO_INSTANCE]
        return [row for row in rows if isinstance(row, ScenarioInstance)]


@pytest.fixture(scope="module")
def reference() -> ReferenceData:
    return generate_reference_data(SEED, CUSTOMERS)


@pytest.fixture(scope="module")
def run(reference: ReferenceData) -> tuple[HistorySummary, CollectedRows]:
    sink = CollectedRows()
    return simulate_history(reference, MONTHS, sink), sink


@pytest.fixture(scope="module")
def transactions(run: tuple[HistorySummary, CollectedRows]) -> list[Transaction]:
    return run[1].transactions()


def test_every_table_gets_rows_and_the_summary_counts_them(
    run: tuple[HistorySummary, CollectedRows],
) -> None:
    summary, sink = run
    for table in (HistoryTable.TRANSACTION, HistoryTable.LOGIN_SESSION, HistoryTable.BENEFICIARY):
        assert sink.rows[table]
    assert summary.row_counts == {table: len(sink.rows[table]) for table in HistoryTable}


def test_simulation_covers_whole_calendar_months(
    run: tuple[HistorySummary, CollectedRows], transactions: list[Transaction]
) -> None:
    summary, _ = run
    assert (summary.first_day.isoformat(), summary.last_day.isoformat()) == (
        "2026-01-01",
        "2026-01-31",
    )
    sa_days = {to_sast(t.event_time).date() for t in transactions}
    assert min(sa_days) == summary.first_day
    # A payment confirmed just before midnight can land a few minutes into the next day.
    assert max(sa_days) <= summary.last_day + timedelta(days=1)


def test_transaction_ids_are_consecutive(transactions: list[Transaction]) -> None:
    ids = [t.transaction_id for t in transactions]
    assert ids == [f"TXN-{number:09d}" for number in range(1, len(ids) + 1)]


def test_every_channel_appears(transactions: list[Transaction]) -> None:
    assert {t.channel for t in transactions} == set(Channel)


def test_balances_reconcile_with_the_postings(
    reference: ReferenceData, transactions: list[Transaction]
) -> None:
    accounts = {a.account_id: a for a in reference.accounts}
    balances = {a.account_id: a.opening_balance_cents for a in reference.accounts}

    for transaction in transactions:
        if transaction.status is TransactionStatus.APPROVED:
            change = transaction.amount_cents
            if transaction.direction is Direction.DEBIT:
                change = -change
            if accounts[transaction.account_id].account_type is AccountType.CREDIT_CARD:
                change = -change  # the balance is the amount owed
            balances[transaction.account_id] += change
        assert transaction.balance_after_cents == balances[transaction.account_id]


def test_declines_are_counted_and_rare(
    run: tuple[HistorySummary, CollectedRows], transactions: list[Transaction]
) -> None:
    declined = [t for t in transactions if t.status is TransactionStatus.DECLINED]
    assert run[0].declined_count == len(declined)
    assert 0 < len(declined) < 0.06 * len(transactions)


def test_internal_payments_credit_the_payee_only_when_approved(
    transactions: list[Transaction],
) -> None:
    debits = Counter(
        (t.counterparty_account_id, t.account_id, t.amount_cents, t.event_time)
        for t in transactions
        if t.direction is Direction.DEBIT
        and t.counterparty_account_id is not None
        and t.status is TransactionStatus.APPROVED
    )
    credits = Counter(
        (t.account_id, t.counterparty_account_id, t.amount_cents, t.event_time)
        for t in transactions
        if t.direction is Direction.CREDIT and t.counterparty_account_id is not None
    )
    assert credits
    assert credits == debits


def test_approved_transfers_have_two_legs_and_declined_ones_only_the_debit(
    transactions: list[Transaction],
) -> None:
    legs: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.transfer_group_id is not None:
            legs[transaction.transfer_group_id].append(transaction)
    assert legs

    for group in legs.values():
        debit = group[0]
        assert debit.direction is Direction.DEBIT
        expected_legs = 2 if debit.status is TransactionStatus.APPROVED else 1
        assert len(group) == expected_legs


def test_references_point_at_rows_that_exist(
    reference: ReferenceData,
    run: tuple[HistorySummary, CollectedRows],
    transactions: list[Transaction],
) -> None:
    rows = run[1].rows
    sessions = {
        s.session_id for s in rows[HistoryTable.LOGIN_SESSION] if isinstance(s, LoginSession)
    }
    payees = {
        b.beneficiary_id for b in rows[HistoryTable.BENEFICIARY] if isinstance(b, Beneficiary)
    }
    new_devices = {d.device_id for d in rows[HistoryTable.DEVICE] if isinstance(d, Device)}
    devices = {d.device_id for d in reference.devices} | new_devices
    cards = {c.card_id for c in reference.cards}
    merchants = {m.merchant_id for m in reference.merchants}

    assert len(sessions) == len(rows[HistoryTable.LOGIN_SESSION])  # IDs are unique
    for t in transactions:
        assert t.session_id is None or t.session_id in sessions
        assert t.beneficiary_id is None or t.beneficiary_id in payees
        assert t.device_id is None or t.device_id in devices
        assert t.card_id is None or t.card_id in cards
        assert t.merchant_id is None or t.merchant_id in merchants


def test_same_seed_gives_the_same_history() -> None:
    def small_run() -> list[Transaction]:
        sink = CollectedRows()
        simulate_history(generate_reference_data(3, 20), 1, sink)
        return sink.transactions()

    assert small_run() == small_run()


def test_every_transaction_has_exactly_one_label(
    run: tuple[HistorySummary, CollectedRows], transactions: list[Transaction]
) -> None:
    labels = run[1].labels()
    assert sorted(label.transaction_id for label in labels) == [
        t.transaction_id for t in transactions
    ]
    assert run[0].fraud_count == sum(label.is_fraud for label in labels) > 0


def test_fraud_labels_match_their_episodes(
    run: tuple[HistorySummary, CollectedRows], transactions: list[Transaction]
) -> None:
    by_id = {t.transaction_id: t for t in transactions}
    scenarios = {s.scenario_id: s for s in run[1].scenarios()}
    stolen: Counter[str] = Counter()

    for label in run[1].labels():
        if not label.is_fraud:
            continue
        assert label.scenario_id is not None
        scenario = scenarios[label.scenario_id]
        transaction = by_id[label.transaction_id]
        assert label.fraud_type is scenario.fraud_type is FraudType.CARD_NOT_PRESENT
        assert scenario.started_at <= transaction.event_time <= scenario.ended_at
        if transaction.status is TransactionStatus.APPROVED:
            stolen[label.scenario_id] += transaction.amount_cents

    for scenario_id, scenario in scenarios.items():
        assert scenario.total_amount_cents == stolen[scenario_id]


def test_fraud_can_be_switched_off(reference: ReferenceData) -> None:
    sink = CollectedRows()
    summary = simulate_history(reference, MONTHS, sink, FraudSettings(0.0))

    assert summary.fraud_count == 0
    assert not sink.scenarios()
    assert not any(label.is_fraud for label in sink.labels())


def test_months_must_be_positive(reference: ReferenceData) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        simulate_history(reference, 0, CollectedRows())
