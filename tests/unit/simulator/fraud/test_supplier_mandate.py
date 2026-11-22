import random
from datetime import UTC, date, datetime, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.beneficiaries import BeneficiaryEventType
from sentinel.domain.customers import CustomerType
from sentinel.domain.labels import FraudType
from sentinel.simulator.fraud.episodes import FraudActions, Victim
from sentinel.simulator.fraud.supplier_mandate import (
    INVOICE_DELAY_DAYS,
    SupplierMandate,
    SupplierMandateEpisode,
)
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import PAYMENT_DELAY_SECONDS, PlannedPayment
from sentinel.simulator.unusual_events import ADD_PAYEE_DELAY_SECONDS, EventIds

START = datetime(2026, 1, 14, 6, 30, tzinfo=UTC)  # a Wednesday
RICH = 10**12
REPORTED_AT = datetime(2026, 2, 20, 9, 15, tzinfo=UTC)


class AccountBalances:
    """The same amount available in every account."""

    def available_cents(self, account_id: str) -> int:
        return RICH


def scenario() -> SupplierMandate:
    return SupplierMandate(
        EventIds(
            sessions=IdSequence("SES"),
            devices=IdSequence("DEV"),
            beneficiaries=IdSequence("BEN"),
            beneficiary_events=IdSequence("BEV"),
            sim_swaps=IdSequence("SIM"),
        )
    )


@pytest.fixture(scope="module")
def episodes(victims: list[Victim]) -> list[SupplierMandateEpisode]:
    mandates = scenario()
    rng = random.Random(7)
    planned = []
    for number in range(1, 101):
        victim = mandates.choose_victim(rng, victims, START)
        assert victim is not None
        planned.append(mandates.plan(rng, victim, START, f"SCN-{number:07d}"))
    return planned


def sa_day(moment: datetime) -> date:
    return to_sast(moment).date()


def test_only_businesses_with_a_supplier_at_another_bank_are_targeted(
    victims: list[Victim], episodes: list[SupplierMandateEpisode]
) -> None:
    individuals = [v for v in victims if v.customer.customer_type is not CustomerType.BUSINESS]
    assert scenario().choose_victim(random.Random(1), individuals, START) is None
    assert len({e.victim_customer_id for e in episodes}) > 1
    for episode in episodes:
        assert episode.victim.customer.customer_type is CustomerType.BUSINESS
        assert episode.payee in episode.victim.payments.beneficiaries
        assert episode.payee.payee_internal_account_id is None


def test_a_victim_without_a_supplier_cannot_be_planned(victims: list[Victim]) -> None:
    individual = next(v for v in victims if v.customer.customer_type is CustomerType.INDIVIDUAL)
    with pytest.raises(SimulationError, match="no supplier"):
        scenario().plan(random.Random(2), individual, START, "SCN-0000001")


def test_the_change_comes_first_and_the_invoice_is_paid_days_later(
    episodes: list[SupplierMandateEpisode],
) -> None:
    for episode in episodes:
        delay_days = (sa_day(episode.invoice_logged_in_at) - sa_day(START)).days
        assert sa_day(episode.logged_in_at) == sa_day(START)
        assert INVOICE_DELAY_DAYS[0] <= delay_days <= INVOICE_DELAY_DAYS[1]
        saved = (episode.changed_at - episode.logged_in_at).total_seconds()
        assert ADD_PAYEE_DELAY_SECONDS[0] <= saved <= ADD_PAYEE_DELAY_SECONDS[1]
        paid = (episode.invoice_paid_at - episode.invoice_logged_in_at).total_seconds()
        assert PAYMENT_DELAY_SECONDS[0] <= paid <= PAYMENT_DELAY_SECONDS[1]
        assert episode.fraud_account_number != episode.payee.payee_account_number
        assert episode.invoice_cents > 0
        assert episode.params == {
            "beneficiary_id": episode.payee.beneficiary_id,
            "invoice_delay_days": delay_days,
            "invoice_cents": episode.invoice_cents,
        }
        assert episode.fraud_type is FraudType.SUPPLIER_MANDATE
        assert episode.days() == {sa_day(episode.logged_in_at), sa_day(episode.invoice_paid_at)}
        assert episode.planned_end() == episode.invoice_paid_at
        assert episode.card_id is None
        assert episode.card_loss is None


def test_invoices_are_paid_to_the_cent(episodes: list[SupplierMandateEpisode]) -> None:
    assert any(episode.invoice_cents % 100 for episode in episodes)


def test_the_business_changes_the_suppliers_number_in_its_own_login(
    episodes: list[SupplierMandateEpisode],
) -> None:
    for episode in episodes[::5]:
        actions = episode.act(sa_day(episode.logged_in_at), AccountBalances())
        (session,), (event,) = actions.sessions, actions.beneficiary_events
        own_devices = {linked.device.device_id for linked in episode.victim.payments.devices}

        assert not actions.attempts
        assert session.customer_id == episode.victim_customer_id
        assert session.device_id is None or session.device_id in own_devices
        assert session.started_at == episode.logged_in_at
        assert event.event_type is BeneficiaryEventType.DETAILS_CHANGED
        assert event.beneficiary_id == episode.payee.beneficiary_id
        assert event.old_account_number == episode.payee.payee_account_number
        assert event.new_account_number == episode.fraud_account_number
        assert (event.session_id, event.event_time) == (session.session_id, episode.changed_at)


def test_the_invoice_is_paid_to_the_supplier_in_a_later_login(
    episodes: list[SupplierMandateEpisode],
) -> None:
    for episode in episodes[::5]:
        actions = episode.act(sa_day(episode.invoice_logged_in_at), AccountBalances())
        (session,), (payment,) = actions.sessions, actions.attempts
        assert isinstance(payment, PlannedPayment)
        debit = payment.debit

        assert not actions.beneficiary_events
        assert session.started_at == episode.invoice_logged_in_at
        assert payment.credit is None  # the fraudster banks elsewhere
        assert debit.account_id == episode.victim.payments.main_account.account_id
        assert debit.beneficiary_id == episode.payee.beneficiary_id
        assert debit.amount_cents == episode.invoice_cents
        assert (debit.event_time, debit.session_id) == (episode.invoice_paid_at, session.session_id)


def test_nothing_happens_on_other_days(episodes: list[SupplierMandateEpisode]) -> None:
    episode = episodes[0]
    day_before = to_sast(episode.logged_in_at).date() - timedelta(days=1)
    assert episode.act(day_before, AccountBalances()) == FraudActions()


def test_the_payee_pays_the_fraudster_from_the_change_until_the_fix(
    episodes: list[SupplierMandateEpisode],
) -> None:
    episode = episodes[0]
    payee_id = episode.payee.beneficiary_id
    changed_at = episode.changed_at
    hijack = episode.hijack(REPORTED_AT)
    restored_at = REPORTED_AT + episode.restore_delay
    second = timedelta(seconds=1)

    assert (hijack.scenario_id, hijack.beneficiary_id) == (episode.scenario_id, payee_id)
    assert hijack.restored_at == restored_at
    assert not hijack.misdirects(payee_id, changed_at - second)
    assert hijack.misdirects(payee_id, changed_at)
    assert hijack.misdirects(payee_id, restored_at - second)
    assert not hijack.misdirects(payee_id, restored_at)
    assert not hijack.misdirects("BEN-9999999", changed_at + second)
    assert not hijack.misdirects(None, changed_at + second)

    never_fixed = episode.hijack(None)
    assert never_fixed.restored_at is None
    assert never_fixed.misdirects(payee_id, changed_at + timedelta(days=3650))


def test_the_business_puts_the_real_number_back_when_it_finds_out(
    episodes: list[SupplierMandateEpisode],
) -> None:
    episode = episodes[0]
    session, event = episode.restore(REPORTED_AT)

    assert session.customer_id == episode.victim_customer_id
    assert session.started_at == REPORTED_AT
    assert event.event_type is BeneficiaryEventType.DETAILS_CHANGED
    assert event.beneficiary_id == episode.payee.beneficiary_id
    assert event.old_account_number == episode.fraud_account_number
    assert event.new_account_number == episode.payee.payee_account_number
    assert event.session_id == session.session_id
    assert event.event_time == REPORTED_AT + episode.restore_delay
    delay = episode.restore_delay.total_seconds()
    assert ADD_PAYEE_DELAY_SECONDS[0] <= delay <= ADD_PAYEE_DELAY_SECONDS[1]
