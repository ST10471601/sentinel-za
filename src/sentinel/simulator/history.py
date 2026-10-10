"""Run the simulation day by day: every customer's activity, settled through the ledger."""

import logging
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sentinel.core.datetimes import add_months, to_sast
from sentinel.domain.accounts import Account, Card, CardType
from sentinel.domain.base import DomainModel
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent
from sentinel.domain.customers import Customer
from sentinel.domain.devices import CustomerDevice, Device
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import Direction, Transaction, TransactionStatus
from sentinel.simulator.beneficiaries import generate_beneficiaries
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import (
    CardHabits,
    MerchantIndex,
    card_drafts,
    plan_card_habits,
)
from sentinel.simulator.drafts import TransactionDraft, to_transaction
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.ledger import Ledger
from sentinel.simulator.payments import (
    PaymentHabits,
    PaymentIds,
    PlannedPayment,
    payment_activity,
    plan_payment_habits,
)
from sentinel.simulator.randomness import make_rng
from sentinel.simulator.recurring import (
    RecurringPlan,
    find_main_account,
    plan_recurring,
    recurring_drafts,
)
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.sessions import LinkedDevice, group_devices_by_customer
from sentinel.simulator.spending import activity_weight, last_pay_date
from sentinel.simulator.unusual_events import (
    BigPurchase,
    EventIds,
    NewPayee,
    NewPhone,
    Trip,
    UnusualEvent,
    add_and_pay_new_payee,
    away_customer,
    big_purchase_draft,
    plan_unusual_events,
    register_new_phone,
    trip_on,
)

TRANSACTION_ID_DIGITS = 9

type DayEvent = BigPurchase | NewPhone | NewPayee  # events that happen on a single day

logger = logging.getLogger(__name__)


class HistoryTable(StrEnum):
    """Tables the history simulation produces."""

    TRANSACTION = "transaction"
    LOGIN_SESSION = "login_session"
    BENEFICIARY = "beneficiary"
    BENEFICIARY_EVENT = "beneficiary_event"
    DEVICE = "device"  # devices registered during the simulation
    CUSTOMER_DEVICE = "customer_device"


MODEL_BY_TABLE: dict[HistoryTable, type[DomainModel]] = {
    HistoryTable.TRANSACTION: Transaction,
    HistoryTable.LOGIN_SESSION: LoginSession,
    HistoryTable.BENEFICIARY: Beneficiary,
    HistoryTable.BENEFICIARY_EVENT: BeneficiaryEvent,
    HistoryTable.DEVICE: Device,
    HistoryTable.CUSTOMER_DEVICE: CustomerDevice,
}


class RowSink(Protocol):
    """Where generated rows go, such as Parquet files or a list in a test."""

    def add(self, table: HistoryTable, rows: Sequence[DomainModel]) -> None:
        """Accept rows for one table, in the order they were generated."""


@dataclass(frozen=True, slots=True)
class HistorySummary:
    """What a run produced."""

    first_day: date
    last_day: date
    row_counts: dict[HistoryTable, int]
    declined_count: int


@dataclass(slots=True)
class _CustomerPlan:
    """Everything fixed for one customer before day one. Payment habits change with events."""

    customer: Customer
    recurring: RecurringPlan
    cards: tuple[Card, ...]
    card_habits: CardHabits
    payments: PaymentHabits
    events: tuple[UnusualEvent, ...]
    events_by_day: dict[date, list[DayEvent]]  # everything except trips, by day
    trip_habits: dict[Trip, CardHabits] = field(default_factory=dict)


@dataclass(slots=True)
class _DayOutput:
    """Everything one day produced, before the bank settles the transactions."""

    planned: list[TransactionDraft | PlannedPayment] = field(default_factory=list)
    sessions: list[LoginSession] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    device_links: list[CustomerDevice] = field(default_factory=list)
    beneficiaries: list[Beneficiary] = field(default_factory=list)
    beneficiary_events: list[BeneficiaryEvent] = field(default_factory=list)


def simulate_history(reference: ReferenceData, months: int, sink: RowSink) -> HistorySummary:
    """Simulate ``months`` of activity from the reference data's start date."""
    if months < 1:
        raise ValueError(f"months must be at least 1, got {months}")
    return _Simulation(reference, months, sink).run()


class _Simulation:
    """One simulation run. Holds the ledger, ID counters and each customer's plan."""

    def __init__(self, reference: ReferenceData, months: int, sink: RowSink) -> None:
        self._sink = sink
        self._first_day = to_sast(reference.simulation_start).date()
        self._last_day = add_months(self._first_day, months) - timedelta(days=1)
        self._ledger = Ledger(reference.accounts, reference.cards)
        self._merchants = MerchantIndex(reference.merchants)
        self._rng = make_rng(reference.seed, "history")
        self._counts: dict[HistoryTable, int] = dict.fromkeys(HistoryTable, 0)
        self._declined_count = 0
        self._transaction_count = 0

        accounts = _group(reference.accounts, lambda a: a.customer_id)
        owner = {account.account_id: account.customer_id for account in reference.accounts}
        cards = _group(reference.cards, lambda c: owner[c.account_id])
        devices = group_devices_by_customer(reference.devices, reference.customer_devices)

        # Payees saved before the start, each in its own login session.
        session_ids = IdSequence("SES")
        payees = generate_beneficiaries(
            make_rng(reference.seed, "beneficiaries"),
            reference.customers,
            {cid: find_main_account(accts) for cid, accts in accounts.items()},
            devices,
            session_ids,
            reference.simulation_start,
        )
        self._emit(HistoryTable.LOGIN_SESSION, payees.sessions)
        self._emit(HistoryTable.BENEFICIARY, payees.beneficiaries)
        self._emit(HistoryTable.BENEFICIARY_EVENT, payees.events)

        # New rows continue the numbering of rows that already exist.
        self._payment_ids = PaymentIds(session_ids, IdSequence("TRF", TRANSACTION_ID_DIGITS))
        self._event_ids = EventIds(
            sessions=session_ids,
            devices=IdSequence("DEV", start_after=len(reference.devices)),
            beneficiaries=IdSequence("BEN", start_after=len(payees.beneficiaries)),
            beneficiary_events=IdSequence("BEV", start_after=len(payees.events)),
        )

        payees_by_customer = _group(payees.beneficiaries, lambda b: b.customer_id)
        plan_rng = make_rng(reference.seed, "plans")
        day_count = (self._last_day - self._first_day).days + 1
        self._plans = [
            self._plan_customer(
                plan_rng,
                customer,
                accounts[customer.customer_id],
                cards[customer.customer_id],
                payees_by_customer.get(customer.customer_id, []),
                devices[customer.customer_id],
                day_count,
            )
            for customer in reference.customers
        ]

    def run(self) -> HistorySummary:
        """Simulate every day in order and send the rows to the sink."""
        day = self._first_day
        while day <= self._last_day:
            self._run_day(day)
            day += timedelta(days=1)

        logger.info("simulated history", extra={t.value: n for t, n in self._counts.items()})
        return HistorySummary(self._first_day, self._last_day, self._counts, self._declined_count)

    def _plan_customer(
        self,
        rng: random.Random,
        customer: Customer,
        accounts: list[Account],
        cards: list[Card],
        payees: list[Beneficiary],
        devices: list[LinkedDevice],
        day_count: int,
    ) -> _CustomerPlan:
        recurring = plan_recurring(rng, customer, find_main_account(accounts))
        events = plan_unusual_events(rng, customer, self._first_day, day_count)
        events_by_day: dict[date, list[DayEvent]] = defaultdict(list)
        for event in events:
            if not isinstance(event, Trip):
                events_by_day[event.day].append(event)

        return _CustomerPlan(
            customer=customer,
            recurring=recurring,
            cards=tuple(cards),
            card_habits=plan_card_habits(rng, customer, cards, self._merchants),
            payments=plan_payment_habits(accounts, payees, devices, recurring.monthly_income_cents),
            events=events,
            events_by_day=events_by_day,
        )

    def _run_day(self, day: date) -> None:
        output = _DayOutput()
        for plan in self._plans:
            self._customer_day(plan, day, output)

        self._emit(HistoryTable.LOGIN_SESSION, output.sessions)
        self._emit(HistoryTable.DEVICE, output.devices)
        self._emit(HistoryTable.CUSTOMER_DEVICE, output.device_links)
        self._emit(HistoryTable.BENEFICIARY, output.beneficiaries)
        self._emit(HistoryTable.BENEFICIARY_EVENT, output.beneficiary_events)
        self._emit(HistoryTable.TRANSACTION, self._settle(output.planned))

    def _customer_day(self, plan: _CustomerPlan, day: date, output: _DayOutput) -> None:
        rng = self._rng
        weight = activity_weight(day, last_pay_date(day, plan.customer.pay_day))
        scheduled = recurring_drafts(rng, plan.recurring, day)
        budget = self._budget(plan, scheduled)
        output.planned += scheduled

        # Unusual events first: a new phone is used for the rest of the day.
        customer = self._handle_events(plan, day, budget, output)
        card_habits = self._card_habits_for(plan, trip_on(plan.events, day))
        output.planned += card_drafts(rng, customer, card_habits, day, weight, budget)

        owed = self._amount_owed(plan.payments)
        payments = payment_activity(
            rng, customer, plan.payments, day, weight, budget, self._payment_ids, owed
        )
        output.sessions += payments.sessions
        output.planned += payments.payments

    def _handle_events(
        self, plan: _CustomerPlan, day: date, budget: DayBudget, output: _DayOutput
    ) -> Customer:
        """Apply the day's unusual events and return the customer as seen that day."""
        trip = trip_on(plan.events, day)
        customer = away_customer(plan.customer, trip.city) if trip else plan.customer

        for event in plan.events_by_day.get(day, []):
            if isinstance(event, NewPhone):
                self._new_phone(plan, customer, event, output)
            elif isinstance(event, BigPurchase):
                card = _card_for_big_purchase(plan.cards)
                draft = big_purchase_draft(
                    self._rng, customer, event, card, self._merchants, budget
                )
                if draft is not None:
                    output.planned.append(draft)
            else:
                self._new_payee(plan, customer, event, budget, output)
        return customer

    def _new_phone(
        self, plan: _CustomerPlan, customer: Customer, event: NewPhone, output: _DayOutput
    ) -> None:
        change = register_new_phone(self._rng, customer, event, self._event_ids)
        linked = LinkedDevice(change.device, change.link.linked_at)
        plan.payments = replace(plan.payments, devices=(*plan.payments.devices, linked))
        output.devices.append(change.device)
        output.device_links.append(change.link)
        output.sessions.append(change.session)

    def _new_payee(
        self,
        plan: _CustomerPlan,
        customer: Customer,
        event: NewPayee,
        budget: DayBudget,
        output: _DayOutput,
    ) -> None:
        # The new payee is a once-off, so it isn't added to the customer's regular payees.
        change = add_and_pay_new_payee(
            self._rng, customer, event, plan.payments, self._event_ids, budget
        )
        if change is None:
            return
        output.beneficiaries.append(change.beneficiary)
        output.beneficiary_events.append(change.event)
        output.sessions.append(change.session)
        output.planned.append(change.payment)

    def _card_habits_for(self, plan: _CustomerPlan, trip: Trip | None) -> CardHabits:
        """Usual habits at home; on a trip, favourite shops in the destination city."""
        if trip is None:
            return plan.card_habits
        if trip not in plan.trip_habits:
            away = away_customer(plan.customer, trip.city)
            plan.trip_habits[trip] = plan_card_habits(self._rng, away, plan.cards, self._merchants)
        return plan.trip_habits[trip]

    def _budget(self, plan: _CustomerPlan, scheduled: list[TransactionDraft]) -> DayBudget:
        """Start from what each account has, plus income landing today."""
        habits = plan.payments
        accounts = [a for a in (habits.main_account, habits.credit_account) if a is not None]
        available = {a.account_id: self._ledger.available_cents(a.account_id) for a in accounts}
        for draft in scheduled:
            if draft.direction is Direction.CREDIT and draft.account_id in available:
                available[draft.account_id] += draft.amount_cents
        return DayBudget(self._rng, available)

    def _amount_owed(self, habits: PaymentHabits) -> int:
        if habits.credit_account is None:
            return 0
        return max(self._ledger.balance_cents(habits.credit_account.account_id), 0)

    def _settle(self, planned: list[TransactionDraft | PlannedPayment]) -> list[Transaction]:
        """Settle the day's transactions in time order, as the bank would."""
        planned.sort(key=_event_time)
        transactions: list[Transaction] = []
        for item in planned:
            if isinstance(item, PlannedPayment):
                debit = self._post(item.debit)
                transactions.append(debit)
                # The payee is only credited if the payment went through.
                if item.credit is not None and debit.status is TransactionStatus.APPROVED:
                    transactions.append(self._post(item.credit))
            else:
                transactions.append(self._post(item))
        return transactions

    def _post(self, draft: TransactionDraft) -> Transaction:
        settlement = self._ledger.settle(draft)
        if settlement.status is TransactionStatus.DECLINED:
            self._declined_count += 1
        self._transaction_count += 1
        return to_transaction(draft, self._transaction_count, settlement)

    def _emit(self, table: HistoryTable, rows: Sequence[DomainModel]) -> None:
        if rows:
            self._sink.add(table, rows)
            self._counts[table] += len(rows)


def _event_time(item: TransactionDraft | PlannedPayment) -> datetime:
    return item.debit.event_time if isinstance(item, PlannedPayment) else item.event_time


def _card_for_big_purchase(cards: Sequence[Card]) -> Card:
    """Big purchases go on the credit card when there is one, else the debit card."""
    by_type = {card.card_type: card for card in cards}
    return by_type.get(CardType.CREDIT) or by_type[CardType.DEBIT]


def _group[T](rows: Sequence[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped
