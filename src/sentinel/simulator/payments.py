"""Digital payments: EFT and PayShap to saved payees, own-account transfers and logins."""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sentinel.domain.accounts import Account, AccountStatus, AccountType
from sentinel.domain.beneficiaries import Beneficiary
from sentinel.domain.customers import Customer
from sentinel.domain.sessions import LoginSession, SessionAuthMethod
from sentinel.domain.transactions import Channel, Direction, TransactionAuthMethod
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.randomness import chance
from sentinel.simulator.recurring import find_main_account
from sentinel.simulator.sessions import LinkedDevice, devices_linked_by, start_session
from sentinel.simulator.spending import (
    SPENDING_BY_BAND,
    last_pay_date,
    pick_event_time,
    sample_amount,
    sample_daily_count,
)

HOME_COUNTRY = "ZA"
BALANCE_CHECKS_PER_MONTH = 6  # logins with no payment
PAYMENT_DELAY_SECONDS = (20, 300)  # from logging in to confirming the payment
OTP_THRESHOLD_CENTS = 300_000  # simulation assumption: payments over R3,000 need an OTP

# Own-account transfers happen the day after income lands.
DAYS_AFTER_PAY_FOR_TRANSFERS = 1
SAVINGS_TOPUP_CHANCE = 0.5
SAVINGS_TOPUP_SHARE = (0.05, 0.15)  # of monthly income
MIN_REPAYMENT_CENTS = 10_000  # R100; smaller amounts owed are left for next month


@dataclass(frozen=True, slots=True)
class PaymentHabits:
    """The accounts, payees and devices a customer pays from."""

    main_account: Account
    savings_account: Account | None  # only an active one; dormant accounts stay quiet
    credit_account: Account | None
    beneficiaries: tuple[Beneficiary, ...]
    devices: tuple[LinkedDevice, ...]
    monthly_income_cents: int


@dataclass(frozen=True, slots=True)
class PlannedPayment:
    """A payment and, when the money stays at our bank, the credit it causes.

    The credit is posted only if the bank approves the debit.
    """

    debit: TransactionDraft
    credit: TransactionDraft | None


@dataclass(slots=True)
class DayPayments:
    """Logins and payments for one customer on one day."""

    sessions: list[LoginSession] = field(default_factory=list)
    payments: list[PlannedPayment] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PaymentIds:
    """Shared ID sequences for sessions and internal transfers."""

    sessions: IdSequence
    transfer_groups: IdSequence


@dataclass(frozen=True, slots=True)
class _Payer:
    """What every helper needs to act for one customer."""

    rng: random.Random
    customer: Customer
    habits: PaymentHabits
    ids: PaymentIds


def plan_payment_habits(
    accounts: Sequence[Account],
    beneficiaries: Sequence[Beneficiary],
    devices: Sequence[LinkedDevice],
    monthly_income_cents: int,
) -> PaymentHabits:
    """Collect one customer's accounts, saved payees and devices for making payments."""
    return PaymentHabits(
        main_account=find_main_account(accounts),
        savings_account=_first_active(accounts, AccountType.SAVINGS),
        credit_account=_first_active(accounts, AccountType.CREDIT_CARD),
        beneficiaries=tuple(beneficiaries),
        devices=tuple(devices),
        monthly_income_cents=monthly_income_cents,
    )


def payment_activity(
    rng: random.Random,
    customer: Customer,
    habits: PaymentHabits,
    day: date,
    weight: float,
    budget: DayBudget,
    ids: PaymentIds,
    credit_owed_cents: int,
) -> DayPayments:
    """Return the customer's logins and payments for one day.

    ``credit_owed_cents`` is the credit-card balance at the start of the day.
    """
    payer = _Payer(rng, customer, habits, ids)
    activity = DayPayments()
    profile = SPENDING_BY_BAND[customer.income_band]

    for _ in range(sample_daily_count(rng, BALANCE_CHECKS_PER_MONTH, weight)):
        activity.sessions.append(_log_in(payer, pick_event_time(rng, day)))

    if habits.beneficiaries:
        for _ in range(sample_daily_count(rng, profile.payments_per_month, weight)):
            amount = sample_amount(rng, profile.payment_amount)
            if budget.try_spend(habits.main_account.account_id, amount):
                _add_payee_payment(payer, day, amount, activity)

    if (day - last_pay_date(day, customer.pay_day)).days == DAYS_AFTER_PAY_FOR_TRANSFERS:
        _add_own_transfers(payer, day, budget, credit_owed_cents, activity)
    return activity


def _add_payee_payment(payer: _Payer, day: date, amount_cents: int, activity: DayPayments) -> None:
    rng = payer.rng
    session = _log_in(payer, pick_event_time(rng, day))
    payee = rng.choice(payer.habits.beneficiaries)
    payment = pay_payee(
        rng, payer.customer, payer.habits, session, payee, amount_cents, _after_login(rng, session)
    )
    activity.sessions.append(session)
    activity.payments.append(payment)


def pay_payee(
    rng: random.Random,
    customer: Customer,
    habits: PaymentHabits,
    session: LoginSession,
    payee: Beneficiary,
    amount_cents: int,
    event_time: datetime,
) -> PlannedPayment:
    """Pay a saved payee from the main account, within an open login session."""
    # PayShap only up to the account's PayShap limit; bigger payments go by EFT.
    use_payshap = (
        chance(rng, SPENDING_BY_BAND[customer.income_band].payshap_share)
        and amount_cents <= habits.main_account.payshap_daily_limit_cents
    )
    channel = Channel.PAYSHAP if use_payshap else Channel.EFT
    payer_id = habits.main_account.account_id

    debit = TransactionDraft(
        account_id=payer_id,
        direction=Direction.DEBIT,
        amount_cents=amount_cents,
        event_time=event_time,
        channel=channel,
        auth_method=_payment_auth(session, amount_cents),
        country_code=HOME_COUNTRY,
        beneficiary_id=payee.beneficiary_id,
        counterparty_account_id=payee.payee_internal_account_id,
        session_id=session.session_id,
        device_id=session.device_id,
    )
    credit = None
    if payee.payee_internal_account_id is not None:
        credit = TransactionDraft(
            account_id=payee.payee_internal_account_id,
            direction=Direction.CREDIT,
            amount_cents=amount_cents,
            event_time=event_time,
            channel=channel,
            auth_method=TransactionAuthMethod.NONE,
            country_code=HOME_COUNTRY,
            counterparty_account_id=payer_id,
        )
    return PlannedPayment(debit, credit)


def _add_own_transfers(
    payer: _Payer, day: date, budget: DayBudget, credit_owed_cents: int, activity: DayPayments
) -> None:
    """Pay off the credit card and put some money into savings, after payday."""
    rng, habits = payer.rng, payer.habits
    main_id = habits.main_account.account_id
    amounts: list[tuple[Account, int]] = []

    if habits.credit_account is not None:
        repayment = min(credit_owed_cents, max(budget.remaining_cents(main_id), 0))
        if repayment >= MIN_REPAYMENT_CENTS and budget.try_spend(main_id, repayment):
            amounts.append((habits.credit_account, repayment))

    if habits.savings_account is not None and chance(rng, SAVINGS_TOPUP_CHANCE):
        topup = round(habits.monthly_income_cents * rng.uniform(*SAVINGS_TOPUP_SHARE))
        if topup > 0 and budget.try_spend(main_id, topup):
            amounts.append((habits.savings_account, topup))

    if not amounts:
        return
    session = _log_in(payer, pick_event_time(rng, day))
    activity.sessions.append(session)
    for target, amount in amounts:
        activity.payments.append(_own_transfer(payer, session, target, amount))


def _own_transfer(
    payer: _Payer, session: LoginSession, target: Account, amount_cents: int
) -> PlannedPayment:
    """Both legs of a transfer between two of the customer's own accounts."""
    group_id = payer.ids.transfer_groups.next_id()
    event_time = _after_login(payer.rng, session)
    source_id = payer.habits.main_account.account_id

    def leg(account_id: str, direction: Direction, other_id: str) -> TransactionDraft:
        return TransactionDraft(
            account_id=account_id,
            direction=direction,
            amount_cents=amount_cents,
            event_time=event_time,
            channel=Channel.INTERNAL_TRANSFER,
            auth_method=_payment_auth(session, amount_cents),
            country_code=HOME_COUNTRY,
            transfer_group_id=group_id,
            counterparty_account_id=other_id,
            session_id=session.session_id,
            device_id=session.device_id,
        )

    return PlannedPayment(
        debit=leg(source_id, Direction.DEBIT, target.account_id),
        credit=leg(target.account_id, Direction.CREDIT, source_id),
    )


def _log_in(payer: _Payer, started_at: datetime) -> LoginSession:
    devices = devices_linked_by(payer.habits.devices, started_at)
    return start_session(payer.rng, payer.ids.sessions, payer.customer, devices, started_at)


def _after_login(rng: random.Random, session: LoginSession) -> datetime:
    return session.started_at + timedelta(seconds=rng.randint(*PAYMENT_DELAY_SECONDS))


def _payment_auth(session: LoginSession, amount_cents: int) -> TransactionAuthMethod:
    """USSD confirms with a PIN; larger app and web payments need an OTP."""
    if session.auth_method is SessionAuthMethod.PIN:
        return TransactionAuthMethod.PIN
    if amount_cents > OTP_THRESHOLD_CENTS or session.auth_method is not SessionAuthMethod.BIOMETRIC:
        return TransactionAuthMethod.OTP
    return TransactionAuthMethod.BIOMETRIC


def _first_active(accounts: Sequence[Account], account_type: AccountType) -> Account | None:
    for account in accounts:
        if account.account_type is account_type and account.status is AccountStatus.ACTIVE:
            return account
    return None
