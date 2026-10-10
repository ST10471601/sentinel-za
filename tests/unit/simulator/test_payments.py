import random
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Account, AccountStatus, AccountType
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.customers import Customer
from sentinel.domain.sessions import LoginSession
from sentinel.domain.transactions import (
    Channel,
    Direction,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.beneficiaries import PayeeHistory, generate_beneficiaries
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.drafts import Settlement, to_transaction
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.payments import (
    DAYS_AFTER_PAY_FOR_TRANSFERS,
    MIN_REPAYMENT_CENTS,
    OTP_THRESHOLD_CENTS,
    DayPayments,
    PaymentHabits,
    PaymentIds,
    payment_activity,
    plan_payment_habits,
)
from sentinel.simulator.recurring import find_main_account
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData
from sentinel.simulator.sessions import LinkedDevice, group_devices_by_customer
from sentinel.simulator.spending import last_pay_date

MARCH = [date(2026, 3, 1) + timedelta(days=offset) for offset in range(31)]
RICH = 10**12
APPROVED = Settlement(TransactionStatus.APPROVED, None, 0)
SAMPLE_SIZE = 150


@pytest.fixture(scope="module")
def accounts_by_customer(reference_data: ReferenceData) -> dict[str, list[Account]]:
    grouped: dict[str, list[Account]] = defaultdict(list)
    for account in reference_data.accounts:
        grouped[account.customer_id].append(account)
    return grouped


@pytest.fixture(scope="module")
def devices_by_customer(reference_data: ReferenceData) -> dict[str, list[LinkedDevice]]:
    return group_devices_by_customer(reference_data.devices, reference_data.customer_devices)


@pytest.fixture(scope="module")
def payees(
    reference_data: ReferenceData,
    accounts_by_customer: dict[str, list[Account]],
    devices_by_customer: dict[str, list[LinkedDevice]],
) -> PayeeHistory:
    main_accounts = {cid: find_main_account(accts) for cid, accts in accounts_by_customer.items()}
    return generate_beneficiaries(
        random.Random(2),
        reference_data.customers,
        main_accounts,
        devices_by_customer,
        IdSequence("SES"),
        DEFAULT_SIMULATION_START,
    )


@pytest.fixture(scope="module")
def sample(
    reference_data: ReferenceData,
    accounts_by_customer: dict[str, list[Account]],
    devices_by_customer: dict[str, list[LinkedDevice]],
    payees: PayeeHistory,
) -> list[tuple[Customer, PaymentHabits]]:
    payees_by_customer: dict[str, list[Beneficiary]] = defaultdict(list)
    for beneficiary in payees.beneficiaries:
        payees_by_customer[beneficiary.customer_id].append(beneficiary)
    return [
        (
            customer,
            plan_payment_habits(
                accounts_by_customer[customer.customer_id],
                payees_by_customer[customer.customer_id],
                devices_by_customer[customer.customer_id],
                monthly_income_cents=2_000_000,
            ),
        )
        for customer in reference_data.customers[:SAMPLE_SIZE]
    ]


def simulate_month(
    sample: list[tuple[Customer, PaymentHabits]], budget_cents: int = RICH, seed: int = 6
) -> list[tuple[Customer, DayPayments]]:
    generator = random.Random(seed)
    ids = PaymentIds(IdSequence("SES"), IdSequence("TRF", digits=9))
    results = []
    for customer, habits in sample:
        owed = habits.credit_account.opening_balance_cents if habits.credit_account else 0
        for day in MARCH:
            budget = DayBudget(generator, {habits.main_account.account_id: budget_cents})
            activity = payment_activity(generator, customer, habits, day, 1.0, budget, ids, owed)
            results.append((customer, activity))
    return results


@pytest.fixture(scope="module")
def month(sample: list[tuple[Customer, PaymentHabits]]) -> list[tuple[Customer, DayPayments]]:
    return simulate_month(sample)


@pytest.fixture(scope="module")
def sessions_by_id(month: list[tuple[Customer, DayPayments]]) -> dict[str, LoginSession]:
    return {s.session_id: s for _, activity in month for s in activity.sessions}


def test_habits_pick_the_main_account_and_skip_dormant_savings(
    accounts_by_customer: dict[str, list[Account]],
) -> None:
    accounts = next(
        accts
        for accts in accounts_by_customer.values()
        if any(a.account_type is AccountType.SAVINGS for a in accts)
    )
    habits = plan_payment_habits(accounts, [], [], 0)
    assert habits.main_account.account_type in {AccountType.CHEQUE, AccountType.BUSINESS_CURRENT}
    assert habits.savings_account is not None

    dormant = [
        a.model_copy(update={"status": AccountStatus.DORMANT})
        if a.account_type is AccountType.SAVINGS
        else a
        for a in accounts
    ]
    assert plan_payment_habits(dormant, [], [], 0).savings_account is None


def test_every_draft_becomes_a_valid_transaction(
    month: list[tuple[Customer, DayPayments]],
) -> None:
    drafts = [
        draft
        for _, activity in month
        for payment in activity.payments
        for draft in (payment.debit, payment.credit)
        if draft is not None
    ]
    channels = {draft.channel for draft in drafts}
    assert channels == {Channel.EFT, Channel.PAYSHAP, Channel.INTERNAL_TRANSFER}
    for number, draft in enumerate(drafts, start=1):
        to_transaction(draft, number, APPROVED)


def test_payments_go_to_the_customers_own_payees_from_their_own_session(
    sample: list[tuple[Customer, PaymentHabits]],
    month: list[tuple[Customer, DayPayments]],
    sessions_by_id: dict[str, LoginSession],
) -> None:
    habits_by_customer = {customer.customer_id: habits for customer, habits in sample}
    for customer, activity in month:
        habits = habits_by_customer[customer.customer_id]
        own_payees = {b.beneficiary_id for b in habits.beneficiaries}
        for payment in activity.payments:
            debit = payment.debit
            assert debit.account_id == habits.main_account.account_id
            assert debit.session_id is not None
            session = sessions_by_id[debit.session_id]
            assert session.customer_id == customer.customer_id
            assert session.device_id == debit.device_id
            assert session.started_at < debit.event_time
            if debit.channel is not Channel.INTERNAL_TRANSFER:
                assert debit.beneficiary_id in own_payees


def test_payments_to_customers_at_our_bank_credit_their_account(
    sample: list[tuple[Customer, PaymentHabits]],
    month: list[tuple[Customer, DayPayments]],
) -> None:
    payee_account = {
        b.beneficiary_id: b.payee_internal_account_id for _, h in sample for b in h.beneficiaries
    }
    payee_payments = [
        p for _, a in month for p in a.payments if p.debit.channel is not Channel.INTERNAL_TRANSFER
    ]
    internal = [p for p in payee_payments if payee_account[str(p.debit.beneficiary_id)]]
    assert internal
    for payment in payee_payments:
        target = payee_account[str(payment.debit.beneficiary_id)]
        if target is None:
            assert payment.credit is None
            continue
        assert payment.credit is not None
        assert payment.credit.account_id == target
        assert payment.credit.counterparty_account_id == payment.debit.account_id
        assert payment.credit.amount_cents == payment.debit.amount_cents
        assert payment.credit.channel is payment.debit.channel


def test_payshap_stays_within_the_account_limit(
    sample: list[tuple[Customer, PaymentHabits]],
    month: list[tuple[Customer, DayPayments]],
) -> None:
    limit = {h.main_account.account_id: h.main_account.payshap_daily_limit_cents for _, h in sample}
    payshap = [p.debit for _, a in month for p in a.payments if p.debit.channel is Channel.PAYSHAP]
    assert payshap
    assert all(d.amount_cents <= limit[d.account_id] for d in payshap)


def test_payment_auth_follows_channel_and_amount(
    month: list[tuple[Customer, DayPayments]], sessions_by_id: dict[str, LoginSession]
) -> None:
    for _, activity in month:
        for payment in activity.payments:
            debit = payment.debit
            session = sessions_by_id[str(debit.session_id)]
            if session.device_id is None:
                assert debit.auth_method is TransactionAuthMethod.PIN
            elif debit.amount_cents > OTP_THRESHOLD_CENTS:
                assert debit.auth_method is TransactionAuthMethod.OTP


def test_own_transfers_happen_the_day_after_pay_with_matching_legs(
    sample: list[tuple[Customer, PaymentHabits]],
    month: list[tuple[Customer, DayPayments]],
    sessions_by_id: dict[str, LoginSession],
) -> None:
    habits_by_customer = {customer.customer_id: habits for customer, habits in sample}
    transfers = [
        (customer, p)
        for customer, a in month
        for p in a.payments
        if p.debit.channel is Channel.INTERNAL_TRANSFER
    ]
    assert transfers
    for customer, payment in transfers:
        habits = habits_by_customer[customer.customer_id]
        session = sessions_by_id[str(payment.debit.session_id)]
        day = to_sast(session.started_at).date()
        assert (day - last_pay_date(day, customer.pay_day)).days == DAYS_AFTER_PAY_FOR_TRANSFERS

        credit = payment.credit
        assert credit is not None
        assert credit.transfer_group_id == payment.debit.transfer_group_id
        assert credit.direction is Direction.CREDIT
        assert credit.counterparty_account_id == habits.main_account.account_id
        own_targets = {
            account.account_id
            for account in (habits.savings_account, habits.credit_account)
            if account is not None
        }
        assert credit.account_id in own_targets


def test_credit_card_repayment_pays_what_is_owed(
    sample: list[tuple[Customer, PaymentHabits]],
    month: list[tuple[Customer, DayPayments]],
) -> None:
    owed = {
        h.credit_account.account_id: h.credit_account.opening_balance_cents
        for _, h in sample
        if h.credit_account
    }
    repayments = [
        p.credit
        for _, a in month
        for p in a.payments
        if p.credit is not None and p.credit.account_id in owed
    ]
    assert repayments
    for credit in repayments:
        assert credit.amount_cents == owed[credit.account_id] >= MIN_REPAYMENT_CENTS


def test_balance_checks_add_logins_without_payments(
    month: list[tuple[Customer, DayPayments]],
) -> None:
    sessions = sum(len(a.sessions) for _, a in month)
    payments = sum(len(a.payments) for _, a in month)
    assert sessions > payments


def test_an_empty_budget_stops_most_payments(
    sample: list[tuple[Customer, PaymentHabits]],
) -> None:
    def payment_count(results: list[tuple[Customer, DayPayments]]) -> int:
        return sum(len(activity.payments) for _, activity in results)

    broke = simulate_month(sample, budget_cents=0)
    assert payment_count(broke) < 0.2 * payment_count(simulate_month(sample))


def test_customer_without_payees_makes_no_payee_payments(
    sample: list[tuple[Customer, PaymentHabits]],
) -> None:
    customer, habits = sample[0]
    lonely = replace(habits, beneficiaries=(), savings_account=None, credit_account=None)
    ids = PaymentIds(IdSequence("SES"), IdSequence("TRF", digits=9))
    generator = random.Random(1)
    for day in MARCH:
        budget = DayBudget(generator, {lonely.main_account.account_id: RICH})
        assert (
            payment_activity(generator, customer, lonely, day, 3.0, budget, ids, 0).payments == []
        )


def test_same_seed_gives_the_same_payments(
    sample: list[tuple[Customer, PaymentHabits]],
) -> None:
    assert simulate_month(sample[:10], seed=4) == simulate_month(sample[:10], seed=4)
