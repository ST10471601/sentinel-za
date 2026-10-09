"""Money that moves on a schedule: salaries, grants, business receipts and debit orders."""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from sentinel.core.datetimes import SAST, ensure_utc
from sentinel.domain.accounts import Account, AccountType
from sentinel.domain.customers import Customer, IncomeSource
from sentinel.domain.transactions import Channel, Direction, TransactionAuthMethod
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.randomness import random_datetime_between
from sentinel.simulator.spending import (
    SPENDING_BY_BAND,
    TypicalAmount,
    activity_weight,
    last_pay_date,
    pick_event_time,
    pick_monthly_income,
    sample_amount,
    sample_daily_count,
)

MAIN_ACCOUNT_TYPES = frozenset({AccountType.CHEQUE, AccountType.BUSINESS_CURRENT})
HOME_COUNTRY = "ZA"
GRANT_PAYER_REF = "SASSA"

# Payroll and debit-order batches run overnight. Income lands first so a debit order
# collected on payday sees the new balance.
INCOME_WINDOW = (time(0), time(3))
DEBIT_ORDER_WINDOW = (time(3), time(6))

EMPLOYER_COUNT = 400  # several customers share an employer
COLLECTOR_COUNT = 150  # insurers, cellphone networks, gyms, lenders
CLIENT_COUNT = 3_000  # who pays businesses
MAX_DEBIT_ORDER_DELAY_DAYS = 3  # collections land on payday or just after

SIDE_INCOME_DAYS_AFTER_PAY = 14  # mixed earners get a second, smaller credit mid-month
SIDE_INCOME_SHARE = (0.15, 0.30)  # of monthly income
BUSINESS_RECEIPTS_PER_MONTH = 20
RECEIPT_SPREAD = 0.8


@dataclass(frozen=True, slots=True)
class DebitOrder:
    """A fixed monthly collection, such as insurance or a phone contract."""

    collector_ref: str
    amount_cents: int
    days_after_pay: int


@dataclass(frozen=True, slots=True)
class RecurringPlan:
    """A customer's income and debit orders, fixed for the whole simulation."""

    account_id: str  # income lands here and debit orders are collected from here
    income_source: IncomeSource
    pay_day: int
    monthly_income_cents: int
    payer_ref: str | None  # employer or SASSA; businesses are paid by many clients
    side_income_cents: int  # 0 unless the customer has mixed income
    debit_orders: tuple[DebitOrder, ...]


def find_main_account(accounts: Sequence[Account]) -> Account:
    """Return the customer's cheque or business current account."""
    for account in accounts:
        if account.account_type in MAIN_ACCOUNT_TYPES:
            return account
    raise ValueError("customer has no cheque or business current account")


def plan_recurring(rng: random.Random, customer: Customer, main_account: Account) -> RecurringPlan:
    """Fix a customer's income and debit orders for the simulation."""
    monthly_income = pick_monthly_income(rng, customer)
    side_income = 0
    if customer.income_source is IncomeSource.MIXED:
        side_income = round(monthly_income * rng.uniform(*SIDE_INCOME_SHARE))

    return RecurringPlan(
        account_id=main_account.account_id,
        income_source=customer.income_source,
        pay_day=customer.pay_day,
        monthly_income_cents=monthly_income,
        payer_ref=_pick_payer_ref(rng, customer.income_source),
        side_income_cents=side_income,
        debit_orders=_pick_debit_orders(rng, customer),
    )


def recurring_drafts(rng: random.Random, plan: RecurringPlan, day: date) -> list[TransactionDraft]:
    """Return the scheduled credits and debit orders for one SA calendar day."""
    last_paid = last_pay_date(day, plan.pay_day)
    days_since_pay = (day - last_paid).days
    drafts: list[TransactionDraft] = []

    if plan.income_source is IncomeSource.BUSINESS:
        drafts += _business_receipts(rng, plan, day, activity_weight(day, last_paid))
    elif days_since_pay == 0:
        drafts.append(_income_credit(rng, plan, day))

    if plan.side_income_cents and days_since_pay == SIDE_INCOME_DAYS_AFTER_PAY:
        drafts.append(_side_income(rng, plan, day))

    drafts += [
        _debit_order(rng, plan, order, day)
        for order in plan.debit_orders
        if order.days_after_pay == days_since_pay
    ]
    return drafts


def _pick_payer_ref(rng: random.Random, income_source: IncomeSource) -> str | None:
    if income_source is IncomeSource.GRANT:
        return GRANT_PAYER_REF
    if income_source is IncomeSource.BUSINESS:
        return None
    return f"EMP-{rng.randint(1, EMPLOYER_COUNT):04d}"


def _pick_debit_orders(rng: random.Random, customer: Customer) -> tuple[DebitOrder, ...]:
    profile = SPENDING_BY_BAND[customer.income_band]
    count = rng.randint(*profile.debit_order_range)
    return tuple(
        DebitOrder(
            collector_ref=f"DO-{rng.randint(1, COLLECTOR_COUNT):04d}",
            amount_cents=sample_amount(rng, profile.debit_order_amount),
            days_after_pay=rng.randint(0, MAX_DEBIT_ORDER_DELAY_DAYS),
        )
        for _ in range(count)
    )


def _income_credit(rng: random.Random, plan: RecurringPlan, day: date) -> TransactionDraft:
    return _credit(
        plan,
        amount_cents=plan.monthly_income_cents,
        event_time=_time_in_window(rng, day, INCOME_WINDOW),
        channel=Channel.SALARY_CREDIT,
        counterparty_ref=plan.payer_ref,
    )


def _side_income(rng: random.Random, plan: RecurringPlan, day: date) -> TransactionDraft:
    return _credit(
        plan,
        amount_cents=plan.side_income_cents,
        event_time=pick_event_time(rng, day),
        channel=Channel.EFT,
        counterparty_ref=f"EXT-{rng.randint(1, CLIENT_COUNT):04d}",
    )


def _business_receipts(
    rng: random.Random, plan: RecurringPlan, day: date, weight: float
) -> list[TransactionDraft]:
    # A log-normal's mean sits above its median, so lower the median to keep the
    # month's receipts adding up to the planned turnover.
    mean_receipt = plan.monthly_income_cents / BUSINESS_RECEIPTS_PER_MONTH
    typical = TypicalAmount(
        median_cents=round(mean_receipt * math.exp(-(RECEIPT_SPREAD**2) / 2)),
        spread=RECEIPT_SPREAD,
    )
    count = sample_daily_count(rng, BUSINESS_RECEIPTS_PER_MONTH, weight)
    return [
        _credit(
            plan,
            amount_cents=sample_amount(rng, typical),
            event_time=pick_event_time(rng, day),
            channel=Channel.EFT,
            counterparty_ref=f"CLI-{rng.randint(1, CLIENT_COUNT):04d}",
        )
        for _ in range(count)
    ]


def _debit_order(
    rng: random.Random, plan: RecurringPlan, order: DebitOrder, day: date
) -> TransactionDraft:
    return TransactionDraft(
        account_id=plan.account_id,
        direction=Direction.DEBIT,
        amount_cents=order.amount_cents,
        event_time=_time_in_window(rng, day, DEBIT_ORDER_WINDOW),
        channel=Channel.DEBIT_ORDER,
        auth_method=TransactionAuthMethod.NONE,  # authorised once when the mandate was signed
        country_code=HOME_COUNTRY,
        counterparty_external_ref=order.collector_ref,
    )


def _credit(
    plan: RecurringPlan,
    *,
    amount_cents: int,
    event_time: datetime,
    channel: Channel,
    counterparty_ref: str | None,
) -> TransactionDraft:
    return TransactionDraft(
        account_id=plan.account_id,
        direction=Direction.CREDIT,
        amount_cents=amount_cents,
        event_time=event_time,
        channel=channel,
        auth_method=TransactionAuthMethod.NONE,
        country_code=HOME_COUNTRY,
        counterparty_external_ref=counterparty_ref,
    )


def _time_in_window(rng: random.Random, day: date, window: tuple[time, time]) -> datetime:
    """A random UTC time between two SA clock times on ``day``."""
    start = datetime.combine(day, window[0], tzinfo=SAST)
    end = datetime.combine(day, window[1], tzinfo=SAST)
    return ensure_utc(random_datetime_between(rng, start, end))
