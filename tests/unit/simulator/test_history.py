import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import AccountType, Card, CardEvent, CardEventType
from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.base import DomainModel
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent
from sentinel.domain.devices import CustomerDevice, Device, DeviceLinkMethod, DevicePlatform
from sentinel.domain.labels import (
    BalanceEnquiryLabel,
    FraudType,
    LoginSessionLabel,
    ScenarioInstance,
    SimSwapLabel,
    TransactionLabel,
)
from sentinel.domain.merchants import Merchant
from sentinel.domain.sessions import LoginSession
from sentinel.domain.sim_swaps import SimSwapEvent
from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    Transaction,
    TransactionStatus,
)
from sentinel.simulator import history, unusual_events
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_replacement import REPLACEMENT_DAYS
from sentinel.simulator.fraud.episodes import (
    Balances,
    FraudActions,
    ParamValue,
    Scenario,
    Victim,
)
from sentinel.simulator.fraud.planning import FRAUD_MIX, FraudSettings
from sentinel.simulator.history import HistorySummary, HistoryTable, simulate_history
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data
from sentinel.simulator.unusual_events import (
    NETWORK_SHARES,
    EventIds,
    NewPayee,
    NewPhone,
    add_and_pay_new_payee,
    notify_sim_swap,
    register_new_phone,
)

SEED = 21
CUSTOMERS = 150
MONTHS = 1
INJECTED_FRAUD_TYPES = {
    FraudType.CARD_NOT_PRESENT,
    FraudType.CARD_PRESENT_LOST_STOLEN,
    FraudType.CARD_PRESENT_COUNTERFEIT,
    FraudType.SIM_SWAP_TAKEOVER,
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

    def sim_swaps(self) -> list[SimSwapEvent]:
        rows = self.rows[HistoryTable.SIM_SWAP_EVENT]
        return [row for row in rows if isinstance(row, SimSwapEvent)]

    def device_links(self) -> list[CustomerDevice]:
        rows = self.rows[HistoryTable.CUSTOMER_DEVICE]
        return [row for row in rows if isinstance(row, CustomerDevice)]

    def sessions(self) -> list[LoginSession]:
        rows = self.rows[HistoryTable.LOGIN_SESSION]
        return [row for row in rows if isinstance(row, LoginSession)]

    def session_labels(self) -> list[LoginSessionLabel]:
        rows = self.rows[HistoryTable.LOGIN_SESSION_LABEL]
        return [row for row in rows if isinstance(row, LoginSessionLabel)]

    def sim_swap_labels(self) -> list[SimSwapLabel]:
        rows = self.rows[HistoryTable.SIM_SWAP_LABEL]
        return [row for row in rows if isinstance(row, SimSwapLabel)]

    def beneficiary_events(self) -> list[BeneficiaryEvent]:
        rows = self.rows[HistoryTable.BENEFICIARY_EVENT]
        return [row for row in rows if isinstance(row, BeneficiaryEvent)]


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
    tables = (
        HistoryTable.TRANSACTION,
        HistoryTable.LOGIN_SESSION,
        HistoryTable.BENEFICIARY,
        HistoryTable.SIM_SWAP_EVENT,
        HistoryTable.LOGIN_SESSION_LABEL,
        HistoryTable.SIM_SWAP_LABEL,
    )
    for table in tables:
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
        if (
            transaction.status is TransactionStatus.APPROVED
            and transaction.direction is Direction.DEBIT
            and transaction.channel is not Channel.INTERNAL_TRANSFER
        ):
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


@pytest.fixture(scope="module")
def counterfeit_run(reference: ReferenceData) -> CollectedRows:
    """About half the fraud is counterfeit, over three months, so several sites skim in the run."""
    sink = CollectedRows()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(FRAUD_MIX, FraudType.CARD_PRESENT_COUNTERFEIT, 0.5)
        simulate_history(reference, 3, sink, FraudSettings(300.0, unreported_share=0.0))
    return sink


def skimmed_in_run(
    rows: CollectedRows, reference: ReferenceData
) -> list[tuple[dict[str, str], str]]:
    """Each cloned card's params and ID, for cards skimmed after the data begins."""
    cloned = fraud_cards(rows, FraudType.CARD_PRESENT_COUNTERFEIT).values()
    params = [(json.loads(scenario.params), card_id) for scenario, card_id in cloned]
    return [
        (p, card_id)
        for p, card_id in params
        if datetime.fromisoformat(p["skimmed_at"]) >= reference.simulation_start
    ]


def test_the_card_is_skimmed_during_a_genuine_transaction(
    reference: ReferenceData, counterfeit_run: CollectedRows
) -> None:
    fraud_ids = {label.transaction_id for label in counterfeit_run.labels() if label.is_fraud}
    genuine = [t for t in counterfeit_run.transactions() if t.transaction_id not in fraud_ids]
    skimmed = skimmed_in_run(counterfeit_run, reference)

    assert skimmed
    for params, card_id in skimmed:
        skimmed_at = datetime.fromisoformat(params["skimmed_at"])
        assert any(
            t.card_id == card_id
            and t.merchant_id == params["skim_merchant_id"]
            and t.event_time == skimmed_at
            for t in genuine
        )


def test_a_sites_victims_all_took_cash_there_while_the_skimmer_was_fitted(
    reference: ReferenceData, counterfeit_run: CollectedRows
) -> None:
    fraud_ids = {label.transaction_id for label in counterfeit_run.labels() if label.is_fraud}
    victims_by_site: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for params, card_id in skimmed_in_run(counterfeit_run, reference):
        if "site_opens" in params:
            site = (params["skim_merchant_id"], params["site_opens"], params["site_closes"])
            victims_by_site[site].add(card_id)

    # What the bank sees: several cards with fraud elsewhere all used one ATM in the same weeks.
    assert any(len(cards) > 1 for cards in victims_by_site.values())
    for (atm_id, opens, closes), cards in victims_by_site.items():
        used_there = {
            t.card_id
            for t in counterfeit_run.transactions()
            if t.transaction_id not in fraud_ids
            and t.merchant_id == atm_id
            and opens <= to_sast(t.event_time).date().isoformat() <= closes
        }
        assert cards <= used_there


def test_a_crews_clones_pass_through_one_atm_within_minutes(
    counterfeit_run: CollectedRows,
) -> None:
    fraud = {label.transaction_id: label for label in counterfeit_run.labels() if label.is_fraud}
    visits = [
        (t.merchant_id, t.event_time, t.card_id)
        for t in counterfeit_run.transactions()
        if t.channel is Channel.ATM
        and t.transaction_id in fraud
        and fraud[t.transaction_id].fraud_type is FraudType.CARD_PRESENT_COUNTERFEIT
    ]

    assert any(
        atm == other_atm
        and card != other_card
        and timedelta(0) < later - moment <= timedelta(minutes=15)
        for atm, moment, card in visits
        for other_atm, later, other_card in visits
    )


def test_the_owner_keeps_using_the_card_while_its_clone_is_swiped(
    counterfeit_run: CollectedRows,
) -> None:
    fraud_ids = {label.transaction_id for label in counterfeit_run.labels() if label.is_fraud}
    by_card: dict[str | None, list[Transaction]] = defaultdict(list)
    for t in counterfeit_run.transactions():
        by_card[t.card_id].append(t)
    in_two_places = 0

    for _, card_id in fraud_cards(counterfeit_run, FraudType.CARD_PRESENT_COUNTERFEIT).values():
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


@pytest.fixture(scope="module")
def swap_heavy_run(reference: ReferenceData) -> CollectedRows:
    """Half the customers swap their SIM each month, so some swap more than once."""
    sink = CollectedRows()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(unusual_events, "SIM_SWAP_RATE", 0.5)
        simulate_history(reference, MONTHS, sink, FraudSettings(0.0))
    return sink


def test_sim_swaps_are_on_the_customers_own_number(
    reference: ReferenceData, swap_heavy_run: CollectedRows
) -> None:
    phone_by_customer = {c.customer_id: c.phone_number for c in reference.customers}
    swaps = swap_heavy_run.sim_swaps()

    assert len({s.sim_swap_id for s in swaps}) == len(swaps)
    for swap in swaps:
        assert swap.phone_number == phone_by_customer[swap.customer_id]


def test_a_customer_stays_on_one_network_across_sim_swaps(swap_heavy_run: CollectedRows) -> None:
    networks_by_customer: dict[str, list[str]] = defaultdict(list)
    for swap in swap_heavy_run.sim_swaps():
        networks_by_customer[swap.customer_id].append(swap.network)

    assert any(len(networks) > 1 for networks in networks_by_customer.values())
    assert all(len(set(networks)) == 1 for networks in networks_by_customer.values())
    assert {n for ns in networks_by_customer.values() for n in ns} == set(NETWORK_SHARES)


def test_about_half_of_sim_swaps_are_followed_by_a_new_device(
    swap_heavy_run: CollectedRows,
) -> None:
    links_by_customer: dict[str, list[datetime]] = defaultdict(list)
    for link in swap_heavy_run.device_links():
        links_by_customer[link.customer_id].append(link.linked_at)

    def has_new_device(swap: SimSwapEvent) -> bool:
        window_end = swap.swapped_at + timedelta(days=4)
        return any(swap.swapped_at < at < window_end for at in links_by_customer[swap.customer_id])

    swaps = swap_heavy_run.sim_swaps()
    # Swaps late in the month lose some new phones to the end of the run.
    assert 0.3 < sum(has_new_device(swap) for swap in swaps) / len(swaps) < 0.6


def test_new_browsers_are_linked_by_otp_then_used_to_log_in(
    reference: ReferenceData, swap_heavy_run: CollectedRows
) -> None:
    platform_by_id = {d.device_id: d.platform for d in reference.devices}
    had_browser = {
        link.customer_id
        for link in reference.customer_devices
        if platform_by_id[link.device_id] is DevicePlatform.WEB
    }
    new_browsers = {
        d.device_id
        for d in swap_heavy_run.rows[HistoryTable.DEVICE]
        if isinstance(d, Device) and d.platform is DevicePlatform.WEB
    }
    links = [link for link in swap_heavy_run.device_links() if link.device_id in new_browsers]
    linked_at = {link.device_id: link.linked_at for link in links}

    assert new_browsers
    for link in links:
        assert link.link_method is DeviceLinkMethod.OTP
        assert link.customer_id in had_browser  # only people who already bank in a browser
    later_logins = [
        s
        for s in swap_heavy_run.sessions()
        if s.device_id in linked_at and s.started_at > linked_at[s.device_id]
    ]
    assert later_logins


@dataclass(frozen=True, slots=True)
class FakeTakeover:
    """Swaps the victim's SIM, registers a new phone, then saves a payee and pays it."""

    scenario_id: str
    victim: Victim
    start: datetime
    ids: EventIds
    fraud_type: FraudType = FraudType.SIM_SWAP_TAKEOVER
    card_id: None = None
    card_loss: None = None

    @property
    def victim_customer_id(self) -> str:
        return self.victim.customer.customer_id

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {}

    def days(self) -> frozenset[date]:
        return frozenset({to_sast(self.start).date()})

    def planned_end(self) -> datetime:
        return self.start

    def act(self, day: date, balances: Balances) -> FraudActions:
        rng = random.Random(self.scenario_id)
        customer, habits = self.victim.customer, self.victim.payments
        swap = notify_sim_swap(rng, self.ids.sim_swaps, customer, "MNO-1", self.start)
        phone = register_new_phone(rng, customer, NewPhone(day, not_before=self.start), self.ids)
        budget = DayBudget(rng, {habits.main_account.account_id: 10**12})  # the ledger decides
        payee = add_and_pay_new_payee(rng, customer, NewPayee(day), habits, self.ids, budget)
        assert payee is not None
        return FraudActions(
            attempts=[payee.payment],
            sim_swaps=[swap],
            devices=[phone.device],
            device_links=[phone.link],
            sessions=[phone.session, payee.session],
            beneficiaries=[payee.beneficiary],
            beneficiary_events=[payee.event],
        )


class FakeTakeoverScenario:
    """Plans fake takeovers against anyone, with the run's shared ID sequences."""

    fraud_type = FraudType.SIM_SWAP_TAKEOVER

    def __init__(self, ids: EventIds) -> None:
        self._ids = ids

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        return rng.choice(candidates)

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> FakeTakeover:
        return FakeTakeover(scenario_id, victim, start, self._ids)


def fake_scenarios(
    merchants: Sequence[Merchant], ids: EventIds, transfer_groups: IdSequence
) -> list[Scenario]:
    return [FakeTakeoverScenario(ids)]


@pytest.fixture(scope="module")
def takeover_run(reference: ReferenceData) -> CollectedRows:
    """Only fake takeovers, all reported, so every identity table also gets fraud rows."""
    sink = CollectedRows()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(history, "fraud_scenarios", fake_scenarios)
        simulate_history(reference, MONTHS, sink, FraudSettings(100.0, unreported_share=0.0))
    return sink


def test_every_login_and_sim_swap_has_exactly_one_label(takeover_run: CollectedRows) -> None:
    session_ids = [s.session_id for s in takeover_run.sessions()]
    swap_ids = [s.sim_swap_id for s in takeover_run.sim_swaps()]

    assert len(set(session_ids)) == len(session_ids)
    assert len(set(swap_ids)) == len(swap_ids)
    assert sorted(label.session_id for label in takeover_run.session_labels()) == sorted(
        session_ids
    )
    assert sorted(label.sim_swap_id for label in takeover_run.sim_swap_labels()) == sorted(swap_ids)


def test_a_fraudsters_logins_and_sim_swaps_are_labelled_with_the_episode(
    takeover_run: CollectedRows,
) -> None:
    scenario_ids = {s.scenario_id for s in takeover_run.scenarios()}
    fraud_sessions = [label for label in takeover_run.session_labels() if label.is_fraud]
    fraud_swaps = [label for label in takeover_run.sim_swap_labels() if label.is_fraud]
    labels: list[LoginSessionLabel | SimSwapLabel] = [*fraud_sessions, *fraud_swaps]

    assert scenario_ids
    assert Counter(label.scenario_id for label in fraud_sessions) == dict.fromkeys(scenario_ids, 2)
    assert Counter(label.scenario_id for label in fraud_swaps) == dict.fromkeys(scenario_ids, 1)
    for label in labels:
        assert label.fraud_type is FraudType.SIM_SWAP_TAKEOVER
        assert label.reported_at is not None


def test_a_fraudsters_device_and_payee_join_back_to_a_fraud_session(
    takeover_run: CollectedRows,
) -> None:
    victim_by_scenario = {s.scenario_id: s.victim_customer_id for s in takeover_run.scenarios()}
    scenario_by_session = {
        label.session_id: label.scenario_id
        for label in takeover_run.session_labels()
        if label.scenario_id is not None
    }
    new_devices = {
        d.device_id for d in takeover_run.rows[HistoryTable.DEVICE] if isinstance(d, Device)
    }
    links = {(link.customer_id, link.device_id) for link in takeover_run.device_links()}

    registrations = [
        s
        for s in takeover_run.sessions()
        if s.session_id in scenario_by_session and s.device_id in new_devices
    ]
    assert len(registrations) == len(victim_by_scenario)
    for session in registrations:
        victim = victim_by_scenario[scenario_by_session[session.session_id]]
        assert (victim, session.device_id) in links

    payee_events = [
        e for e in takeover_run.beneficiary_events() if e.session_id in scenario_by_session
    ]
    fraud_ids = {label.transaction_id for label in takeover_run.labels() if label.is_fraud}
    paid = {t.beneficiary_id for t in takeover_run.transactions() if t.transaction_id in fraud_ids}
    assert len(payee_events) == len(victim_by_scenario)
    assert paid == {e.beneficiary_id for e in payee_events}


@pytest.fixture(scope="module")
def real_takeover_run(reference: ReferenceData) -> CollectedRows:
    """Mostly real SIM swap takeovers, all reported."""
    sink = CollectedRows()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(FRAUD_MIX, FraudType.SIM_SWAP_TAKEOVER, 2.0)
        simulate_history(reference, MONTHS, sink, FraudSettings(300.0, unreported_share=0.0))
    return sink


def takeovers(rows: CollectedRows) -> dict[str, ScenarioInstance]:
    return {
        s.scenario_id: s for s in rows.scenarios() if s.fraud_type is FraudType.SIM_SWAP_TAKEOVER
    }


def test_each_takeover_swaps_the_victims_sim_once_before_logging_in(
    real_takeover_run: CollectedRows,
) -> None:
    episodes = takeovers(real_takeover_run)
    swaps = {s.sim_swap_id: s for s in real_takeover_run.sim_swaps()}
    sessions = {s.session_id: s for s in real_takeover_run.sessions()}
    first_login: dict[str, datetime] = {}
    for label in real_takeover_run.session_labels():
        if label.scenario_id in episodes:
            started_at = sessions[label.session_id].started_at
            first_login[label.scenario_id] = min(
                first_login.get(label.scenario_id, started_at), started_at
            )
    fraud_swaps = [label for label in real_takeover_run.sim_swap_labels() if label.is_fraud]

    assert episodes
    assert Counter(label.scenario_id for label in fraud_swaps) == dict.fromkeys(episodes, 1)
    for swap_label in fraud_swaps:
        assert swap_label.scenario_id is not None
        swap = swaps[swap_label.sim_swap_id]
        assert swap.customer_id == episodes[swap_label.scenario_id].victim_customer_id
        assert swap.swapped_at < first_login[swap_label.scenario_id]


def test_a_takeovers_devices_and_payees_join_back_to_its_logins(
    real_takeover_run: CollectedRows,
) -> None:
    episodes = takeovers(real_takeover_run)
    scenario_by_session = {
        label.session_id: label.scenario_id
        for label in real_takeover_run.session_labels()
        if label.scenario_id in episodes
    }
    new_devices = {
        d.device_id for d in real_takeover_run.rows[HistoryTable.DEVICE] if isinstance(d, Device)
    }
    links = {(link.customer_id, link.device_id) for link in real_takeover_run.device_links()}

    for session in real_takeover_run.sessions():
        scenario_id = scenario_by_session.get(session.session_id)
        if scenario_id is None:
            continue
        victim = episodes[scenario_id].victim_customer_id
        assert session.customer_id == victim
        if session.device_id is not None:
            assert session.device_id in new_devices
            assert (victim, session.device_id) in links

    fraud_payees = {
        e.beneficiary_id
        for e in real_takeover_run.beneficiary_events()
        if e.session_id in scenario_by_session
    }
    fraud_ids = {label.transaction_id for label in real_takeover_run.labels() if label.is_fraud}
    paid = {
        t.beneficiary_id
        for t in real_takeover_run.transactions()
        if t.transaction_id in fraud_ids and t.beneficiary_id is not None
    }
    assert fraud_payees
    assert paid <= fraud_payees


def test_takeover_money_leaves_by_payshap_and_eft_and_the_sweep_is_not_counted(
    real_takeover_run: CollectedRows,
) -> None:
    episodes = takeovers(real_takeover_run)
    by_id = {t.transaction_id: t for t in real_takeover_run.transactions()}
    taken: Counter[str] = Counter()
    channels = set()
    for label in real_takeover_run.labels():
        if label.scenario_id not in episodes:
            continue
        transaction = by_id[label.transaction_id]
        channels.add(transaction.channel)
        if (
            transaction.status is TransactionStatus.APPROVED
            and transaction.direction is Direction.DEBIT
            and transaction.channel is not Channel.INTERNAL_TRANSFER
        ):
            taken[label.scenario_id] += transaction.amount_cents

    assert channels == {Channel.INTERNAL_TRANSFER, Channel.PAYSHAP, Channel.EFT}
    assert any(taken.values())
    for scenario_id, episode in episodes.items():
        assert episode.total_amount_cents == taken[scenario_id]
