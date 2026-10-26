import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import AccountType, Card, CardEvent, CardEventType
from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.base import DomainModel
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.devices import Device
from sentinel.domain.labels import (
    BalanceEnquiryLabel,
    FraudType,
    ScenarioInstance,
    TransactionLabel,
)
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    Transaction,
    TransactionStatus,
)
from sentinel.simulator.card_replacement import REPLACEMENT_DAYS
from sentinel.simulator.fraud.planning import FraudSettings
from sentinel.simulator.history import HistorySummary, HistoryTable, simulate_history
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data

SEED = 21
CUSTOMERS = 150
MONTHS = 1
INJECTED_FRAUD_TYPES = {
    FraudType.CARD_NOT_PRESENT,
    FraudType.CARD_PRESENT_LOST_STOLEN,
    FraudType.CARD_PRESENT_COUNTERFEIT,
}
CARD_AND_PIN_FRAUD_TYPES = {FraudType.CARD_PRESENT_LOST_STOLEN, FraudType.CARD_PRESENT_COUNTERFEIT}
BLOCK_EVENTS = {CardEventType.BLOCKED, CardEventType.REPORTED_LOST}


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

    def cards(self) -> list[Card]:
        return [row for row in self.rows[HistoryTable.CARD] if isinstance(row, Card)]

    def card_events(self) -> list[CardEvent]:
        return [row for row in self.rows[HistoryTable.CARD_EVENT] if isinstance(row, CardEvent)]

    def enquiries(self) -> list[BalanceEnquiry]:
        rows = self.rows[HistoryTable.BALANCE_ENQUIRY]
        return [row for row in rows if isinstance(row, BalanceEnquiry)]

    def enquiry_labels(self) -> list[BalanceEnquiryLabel]:
        rows = self.rows[HistoryTable.BALANCE_ENQUIRY_LABEL]
        return [row for row in rows if isinstance(row, BalanceEnquiryLabel)]


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
    cards = {c.card_id for c in reference.cards} | {c.card_id for c in run[1].cards()}
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
        assert label.fraud_type is scenario.fraud_type
        assert scenario.fraud_type in INJECTED_FRAUD_TYPES
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


@pytest.fixture(scope="module")
def fraud_heavy_run(reference: ReferenceData) -> CollectedRows:
    """Far more fraud than usual, all reported, so plenty of cards are blocked and replaced."""
    sink = CollectedRows()
    simulate_history(reference, 2, sink, FraudSettings(300.0, unreported_share=0.0))
    return sink


def test_reported_card_fraud_blocks_the_card_that_was_used(fraud_heavy_run: CollectedRows) -> None:
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    fraud_cards = {
        t.card_id for t in fraud_heavy_run.transactions() if t.transaction_id in fraud_ids
    }
    blocked = [e for e in fraud_heavy_run.card_events() if e.event_type is CardEventType.BLOCKED]

    assert blocked
    assert {event.card_id for event in blocked} <= fraud_cards


def test_each_blocked_card_is_replaced_on_the_same_account(
    reference: ReferenceData, fraud_heavy_run: CollectedRows
) -> None:
    events = fraud_heavy_run.card_events()
    blocked_at = {e.card_id: e.event_time for e in events if e.event_type in BLOCK_EVENTS}
    issued = [e for e in events if e.event_type is CardEventType.ISSUED]
    all_cards = {c.card_id: c for c in [*reference.cards, *fraud_heavy_run.cards()]}

    assert issued
    assert len(fraud_heavy_run.cards()) == len(issued)
    for event in issued:
        assert event.replaces_card_id is not None
        new, old = all_cards[event.card_id], all_cards[event.replaces_card_id]
        assert (new.account_id, new.card_type) == (old.account_id, old.card_type)
        days = (to_sast(new.issued_at).date() - to_sast(blocked_at[old.card_id]).date()).days
        assert REPLACEMENT_DAYS[0] <= days <= REPLACEMENT_DAYS[1]


def test_owners_stop_using_a_blocked_card_and_move_to_the_new_one(
    fraud_heavy_run: CollectedRows,
) -> None:
    events = fraud_heavy_run.card_events()
    blocked_at = {e.card_id: e.event_time for e in events if e.event_type in BLOCK_EVENTS}
    issued_at = {c.card_id: c.issued_at for c in fraud_heavy_run.cards()}
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    used_replacements = set()

    for t in fraud_heavy_run.transactions():
        if t.transaction_id in fraud_ids or t.card_id is None:
            continue
        if t.card_id in blocked_at:
            assert t.event_time < blocked_at[t.card_id]
        if t.card_id in issued_at:
            assert t.event_time >= issued_at[t.card_id]
            used_replacements.add(t.card_id)
        assert t.decline_reason is not DeclineReason.CARD_BLOCKED

    assert used_replacements


def test_unreported_fraud_blocks_only_cards_reported_lost(reference: ReferenceData) -> None:
    sink = CollectedRows()
    simulate_history(reference, MONTHS, sink, FraudSettings(300.0, unreported_share=1.0))
    blocks = [e for e in sink.card_events() if e.event_type is not CardEventType.ISSUED]

    assert sink.scenarios()
    assert blocks
    assert {e.event_type for e in blocks} == {CardEventType.REPORTED_LOST}


def fraud_cards(
    rows: CollectedRows, fraud_type: FraudType
) -> dict[str, tuple[ScenarioInstance, str]]:
    """Each card fraud episode of one type, with the card the fraudster used."""
    episodes = {s.scenario_id: s for s in rows.scenarios() if s.fraud_type is fraud_type}
    transactions = {t.transaction_id: t for t in rows.transactions()}
    cards: dict[str, tuple[ScenarioInstance, str]] = {}
    for label in rows.labels():
        if label.scenario_id in episodes:
            card_id = transactions[label.transaction_id].card_id
            assert card_id is not None
            cards[label.scenario_id] = (episodes[label.scenario_id], card_id)
    return cards


def call_time(scenario: ScenarioInstance) -> datetime:
    """Roughly when the victim called the bank; params keep the delay in whole minutes."""
    params = json.loads(scenario.params)
    return datetime.fromisoformat(params["lost_at"]) + timedelta(
        minutes=params["call_delay_minutes"]
    )


def test_lost_cards_are_blocked_at_the_call_and_the_thief_is_declined(
    fraud_heavy_run: CollectedRows,
) -> None:
    stolen = fraud_cards(fraud_heavy_run, FraudType.CARD_PRESENT_LOST_STOLEN)
    reported_lost = {
        e.card_id
        for e in fraud_heavy_run.card_events()
        if e.event_type is CardEventType.REPORTED_LOST
    }
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    thief_declines = [
        t
        for t in fraud_heavy_run.transactions()
        if t.transaction_id in fraud_ids and t.decline_reason is DeclineReason.CARD_BLOCKED
    ]

    run_end = max(t.event_time for t in fraud_heavy_run.transactions())
    called_in_run = {
        card_id for scenario, card_id in stolen.values() if call_time(scenario) < run_end
    }

    assert called_in_run
    assert called_in_run <= reported_lost
    assert {t.card_id for t in thief_declines} <= reported_lost
    assert thief_declines


def test_owners_stop_using_a_card_when_it_is_stolen(fraud_heavy_run: CollectedRows) -> None:
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    lost_at = {
        card_id: datetime.fromisoformat(json.loads(scenario.params)["lost_at"])
        for scenario, card_id in fraud_cards(
            fraud_heavy_run, FraudType.CARD_PRESENT_LOST_STOLEN
        ).values()
    }

    for t in fraud_heavy_run.transactions():
        if t.transaction_id not in fraud_ids and t.card_id in lost_at:
            assert t.event_time < lost_at[t.card_id]


def test_the_victim_withdraws_cash_where_the_card_is_swapped(
    fraud_heavy_run: CollectedRows,
) -> None:
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    genuine = [t for t in fraud_heavy_run.transactions() if t.transaction_id not in fraud_ids]
    swaps = 0

    for scenario, card_id in fraud_cards(
        fraud_heavy_run, FraudType.CARD_PRESENT_LOST_STOLEN
    ).values():
        params = json.loads(scenario.params)
        if not params["pin_known"]:
            continue
        lost_at = datetime.fromisoformat(params["lost_at"])
        assert any(
            t.card_id == card_id
            and t.merchant_id == params["theft_atm_id"]
            and t.channel is Channel.ATM
            and timedelta(0) < lost_at - t.event_time <= timedelta(minutes=3)
            for t in genuine
        )
        swaps += 1
    assert swaps


def test_the_card_is_skimmed_during_a_genuine_transaction(fraud_heavy_run: CollectedRows) -> None:
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    genuine = [t for t in fraud_heavy_run.transactions() if t.transaction_id not in fraud_ids]
    cloned = fraud_cards(fraud_heavy_run, FraudType.CARD_PRESENT_COUNTERFEIT)

    assert cloned
    for scenario, card_id in cloned.values():
        params = json.loads(scenario.params)
        skimmed_at = datetime.fromisoformat(params["skimmed_at"])
        assert any(
            t.card_id == card_id
            and t.merchant_id == params["skim_merchant_id"]
            and t.event_time == skimmed_at
            for t in genuine
        )


def test_the_owner_keeps_using_the_card_while_its_clone_is_swiped(
    fraud_heavy_run: CollectedRows,
) -> None:
    fraud_ids = {label.transaction_id for label in fraud_heavy_run.labels() if label.is_fraud}
    by_card: dict[str | None, list[Transaction]] = defaultdict(list)
    for t in fraud_heavy_run.transactions():
        by_card[t.card_id].append(t)
    in_two_places = 0

    for _, card_id in fraud_cards(fraud_heavy_run, FraudType.CARD_PRESENT_COUNTERFEIT).values():
        clone = [t for t in by_card[card_id] if t.transaction_id in fraud_ids]
        own = [t for t in by_card[card_id] if t.transaction_id not in fraud_ids]
        assert {t.entry_mode for t in clone} == {EntryMode.MAGSTRIPE}
        # The impossible-travel signal: the same card used by its owner within a day.
        if any(abs(o.event_time - c.event_time) < timedelta(days=1) for o in own for c in clone):
            in_two_places += 1
    assert in_two_places


def test_every_balance_check_has_exactly_one_label(fraud_heavy_run: CollectedRows) -> None:
    enquiries = fraud_heavy_run.enquiries()
    labels = fraud_heavy_run.enquiry_labels()

    assert enquiries
    assert sorted(label.balance_enquiry_id for label in labels) == [
        e.balance_enquiry_id for e in enquiries
    ]


def test_fraudsters_check_the_balance_on_the_card_they_hold(
    fraud_heavy_run: CollectedRows,
) -> None:
    held = {
        card_id
        for fraud_type in CARD_AND_PIN_FRAUD_TYPES
        for _, card_id in fraud_cards(fraud_heavy_run, fraud_type).values()
    }
    by_id = {e.balance_enquiry_id: e for e in fraud_heavy_run.enquiries()}
    fraud = [label for label in fraud_heavy_run.enquiry_labels() if label.is_fraud]

    assert fraud
    assert len(fraud) < len(by_id) / 2  # customers check far more often than fraudsters
    for label in fraud:
        assert label.fraud_type in CARD_AND_PIN_FRAUD_TYPES
        assert by_id[label.balance_enquiry_id].card_id in held
