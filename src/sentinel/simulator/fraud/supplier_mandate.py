"""Supplier mandate fraud: a business is emailed new bank details for a supplier it pays.

The email looks like it comes from the supplier, often from its own hacked mailbox, so the
business changes the saved payee itself, in its own login on its own device. Every payment to that
payee then lands in the fraudster's account until the real supplier asks why it has not been paid.
SABRIC 2025 gives how common it is (FRAUD_MIX) but not the steps, so the shape below is the usual
pattern and a simulation assumption.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent
from sentinel.domain.customers import CustomerType
from sentinel.domain.labels import FraudType
from sentinel.domain.sessions import LoginSession
from sentinel.simulator.fraud.episodes import Balances, FraudActions, ParamValue, Victim
from sentinel.simulator.identifiers import make_account_number
from sentinel.simulator.payee_changes import details_changed
from sentinel.simulator.payments import PAYMENT_DELAY_SECONDS, pay_payee
from sentinel.simulator.randomness import draw_unique
from sentinel.simulator.sessions import devices_linked_by, start_session
from sentinel.simulator.spending import (
    SPENDING_BY_BAND,
    pick_event_time,
    sample_amount,
    typed_amount,
)
from sentinel.simulator.unusual_events import ADD_PAYEE_DELAY_SECONDS, EventIds

# Simulation assumption: the email asks for an invoice to be paid, and the business pays it in
# one of its next payment runs.
INVOICE_DELAY_DAYS = (1, 7)


@dataclass(frozen=True, slots=True)
class PayeeHijack:
    """A supplier payee that pays the fraudster, from the change until the business fixes it."""

    scenario_id: str
    beneficiary_id: str
    changed_at: datetime
    restored_at: datetime | None  # None when the fraud is never reported

    def misdirects(self, beneficiary_id: str | None, paid_at: datetime) -> bool:
        """Whether a payment to ``beneficiary_id`` at ``paid_at`` lands at the fraudster."""
        if beneficiary_id != self.beneficiary_id or paid_at < self.changed_at:
            return False
        return self.restored_at is None or paid_at < self.restored_at


@dataclass(frozen=True, slots=True)
class SupplierMandateEpisode:
    """One fake email: the supplier's account number changed, then an invoice paid to it."""

    scenario_id: str
    victim: Victim
    params: Mapping[str, ParamValue]
    payee: Beneficiary  # the supplier as saved, with its real account number
    fraud_account_number: str  # at another bank
    logged_in_at: datetime  # the login that changes the details
    changed_at: datetime
    invoice_logged_in_at: datetime
    invoice_paid_at: datetime
    invoice_cents: int
    restore_delay: timedelta  # from the report to the real number being saved again
    ids: EventIds
    seed: int

    fraud_type = FraudType.SUPPLIER_MANDATE

    @property
    def victim_customer_id(self) -> str:
        """The business that is tricked."""
        return self.victim.customer.customer_id

    @property
    def card_id(self) -> None:
        """No card is used, so none is blocked when the fraud is reported."""
        return None

    @property
    def card_loss(self) -> None:
        """The business keeps every card."""
        return None

    def days(self) -> frozenset[date]:
        """The day of the change and the day the invoice is paid."""
        return frozenset({_sa_day(self.logged_in_at), _sa_day(self.invoice_logged_in_at)})

    def planned_end(self) -> datetime:
        """The invoice payment. Later payments to the payee depend on when anyone notices."""
        return self.invoice_paid_at

    def act(self, day: date, balances: Balances) -> FraudActions:
        """Change the supplier's details on the first day; pay the invoice on the second."""
        rng = random.Random(self.seed + day.toordinal())
        actions = FraudActions()
        if day == _sa_day(self.logged_in_at):
            session = self._log_in(rng, self.logged_in_at)
            event = details_changed(
                self.ids,
                self.payee.beneficiary_id,
                self.payee.payee_account_number,
                self.fraud_account_number,
                session,
                self.changed_at,
            )
            actions.sessions.append(session)
            actions.beneficiary_events.append(event)
        if day == _sa_day(self.invoice_logged_in_at):
            session = self._log_in(rng, self.invoice_logged_in_at)
            victim = self.victim
            payment = pay_payee(
                rng,
                victim.customer,
                victim.payments,
                session,
                self.payee,
                self.invoice_cents,
                self.invoice_paid_at,
            )
            actions.sessions.append(session)
            actions.attempts.append(payment)
        return actions

    def hijack(self, reported_at: datetime | None) -> PayeeHijack:
        """How long the payee pays the fraudster, given when the fraud is reported."""
        restored_at = None if reported_at is None else reported_at + self.restore_delay
        return PayeeHijack(
            self.scenario_id, self.payee.beneficiary_id, self.changed_at, restored_at
        )

    def restore(self, reported_at: datetime) -> tuple[LoginSession, BeneficiaryEvent]:
        """The business puts the real account number back as soon as it finds out.

        This is the business's own fix, so the login and change are not fraud.
        """
        rng = random.Random(self.seed + reported_at.toordinal() + 1)
        session = self._log_in(rng, reported_at)
        event = details_changed(
            self.ids,
            self.payee.beneficiary_id,
            self.fraud_account_number,
            self.payee.payee_account_number,
            session,
            reported_at + self.restore_delay,
        )
        return session, event

    def _log_in(self, rng: random.Random, logged_in_at: datetime) -> LoginSession:
        """A login on one of the business's own devices."""
        devices = devices_linked_by(self.victim.payments.devices, logged_in_at)
        return start_session(rng, self.ids.sessions, self.victim.customer, devices, logged_in_at)


class SupplierMandate:
    """Plans supplier mandate fraud against businesses. Its logins and payee events use run IDs."""

    fraud_type = FraudType.SUPPLIER_MANDATE

    def __init__(self, ids: EventIds) -> None:
        self._ids = ids

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """A business with a supplier at another bank and a device to bank on."""
        day = _sa_day(start)
        targets = [v for v in candidates if _suppliers(v) and _devices_before(v, day)]
        return rng.choice(targets) if targets else None

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> SupplierMandateEpisode:
        """Plan the change on the day of ``start`` and the invoice a few days later."""
        suppliers = _suppliers(victim)
        if not suppliers or not _devices_before(victim, _sa_day(start)):
            raise SimulationError(f"{victim.customer.customer_id} has no supplier to redirect")
        payee = rng.choice(suppliers)
        # Same times of day as genuine payee changes and payments, so the hour gives nothing away.
        logged_in_at = pick_event_time(rng, _sa_day(start))
        changed_at = logged_in_at + _seconds(rng, ADD_PAYEE_DELAY_SECONDS)
        fraud_number = draw_unique(lambda: make_account_number(rng), {payee.payee_account_number})
        delay_days = rng.randint(*INVOICE_DELAY_DAYS)
        invoice_logged_in_at = pick_event_time(rng, _sa_day(start) + timedelta(days=delay_days))
        invoice_paid_at = invoice_logged_in_at + _seconds(rng, PAYMENT_DELAY_SECONDS)
        # A real invoice with only the bank details changed, so the amount is a normal one.
        customer = victim.customer
        typical = SPENDING_BY_BAND[customer.income_band].payment_amount
        invoice_cents = typed_amount(rng, customer, sample_amount(rng, typical))
        return SupplierMandateEpisode(
            scenario_id=scenario_id,
            victim=victim,
            params={
                "beneficiary_id": payee.beneficiary_id,
                "invoice_delay_days": delay_days,
                "invoice_cents": invoice_cents,
            },
            payee=payee,
            fraud_account_number=fraud_number,
            logged_in_at=logged_in_at,
            changed_at=changed_at,
            invoice_logged_in_at=invoice_logged_in_at,
            invoice_paid_at=invoice_paid_at,
            invoice_cents=invoice_cents,
            restore_delay=_seconds(rng, ADD_PAYEE_DELAY_SECONDS),
            ids=self._ids,
            seed=rng.getrandbits(32),
        )


def _suppliers(victim: Victim) -> list[Beneficiary]:
    """A business's saved payees at other banks."""
    if victim.customer.customer_type is not CustomerType.BUSINESS:
        return []
    return [p for p in victim.payments.beneficiaries if p.payee_internal_account_id is None]


def _devices_before(victim: Victim, day: date) -> bool:
    """Whether the victim has a device linked before ``day`` starts."""
    day_starts = datetime.combine(day, time(), tzinfo=SAST)
    return bool(devices_linked_by(victim.payments.devices, day_starts))


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
