import random
from dataclasses import replace
from datetime import date, time, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import AccountType
from sentinel.domain.customers import Customer, CustomerType, IncomeBand, IncomeSource
from sentinel.domain.transactions import Channel, Direction, TransactionStatus
from sentinel.simulator.drafts import Settlement, TransactionDraft, to_transaction
from sentinel.simulator.recurring import (
    DEBIT_ORDER_WINDOW,
    GRANT_PAYER_REF,
    INCOME_WINDOW,
    MAX_DEBIT_ORDER_DELAY_DAYS,
    SIDE_INCOME_DAYS_AFTER_PAY,
    DebitOrder,
    RecurringPlan,
    find_main_account,
    plan_recurring,
    recurring_drafts,
)
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.spending import SPENDING_BY_BAND

PAYDAY = date(2026, 2, 25)  # a Wednesday, so no weekend shift
INSURANCE = DebitOrder(collector_ref="DO-0001", amount_cents=45_000, days_after_pay=0)
PHONE = DebitOrder(collector_ref="DO-0002", amount_cents=60_000, days_after_pay=2)
SALARY_PLAN = RecurringPlan(
    account_id="ACC-0000001",
    income_source=IncomeSource.SALARY,
    pay_day=25,
    monthly_income_cents=2_500_000,
    payer_ref="EMP-0001",
    side_income_cents=0,
    debit_orders=(INSURANCE, PHONE),
)
APPROVED = Settlement(TransactionStatus.APPROVED, None, 0)


def rng() -> random.Random:
    return random.Random(11)


def days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def sa_time(draft: TransactionDraft) -> time:
    return to_sast(draft.event_time).time()


def customer_with(
    reference_data: ReferenceData, band: IncomeBand, source: IncomeSource
) -> Customer:
    individual = next(
        customer
        for customer in reference_data.customers
        if customer.customer_type is CustomerType.INDIVIDUAL
    )
    return Customer.model_validate(
        individual.model_dump() | {"income_band": band, "income_source": source}
    )


def business_customer(reference_data: ReferenceData) -> Customer:
    return next(
        customer
        for customer in reference_data.customers
        if customer.customer_type is CustomerType.BUSINESS
    )


def test_main_account_is_the_cheque_or_business_current_account(
    reference_data: ReferenceData,
) -> None:
    accounts = [a for a in reference_data.accounts if a.customer_id == "CUS-0000001"]
    main = find_main_account(list(reversed(accounts)))
    assert main.account_type in {AccountType.CHEQUE, AccountType.BUSINESS_CURRENT}

    savings = [a for a in reference_data.accounts if a.account_type is AccountType.SAVINGS]
    savings_only = savings[:1]
    with pytest.raises(ValueError, match="no cheque"):
        find_main_account(savings_only)


def test_grant_plan_is_paid_by_sassa_in_grant_amounts(reference_data: ReferenceData) -> None:
    customer = customer_with(reference_data, IncomeBand.LOW, IncomeSource.GRANT)
    plan = plan_recurring(rng(), customer, reference_data.accounts[0])
    assert plan.payer_ref == GRANT_PAYER_REF
    assert plan.monthly_income_cents in {240_000, 298_000, 356_000}
    assert plan.side_income_cents == 0


def test_salary_plan_has_an_employer_and_debit_orders_in_range(
    reference_data: ReferenceData,
) -> None:
    customer = customer_with(reference_data, IncomeBand.MIDDLE, IncomeSource.SALARY)
    plan = plan_recurring(rng(), customer, reference_data.accounts[0])

    assert plan.payer_ref is not None and plan.payer_ref.startswith("EMP-")
    low, high = SPENDING_BY_BAND[IncomeBand.MIDDLE].debit_order_range
    assert low <= len(plan.debit_orders) <= high
    assert all(0 <= o.days_after_pay <= MAX_DEBIT_ORDER_DELAY_DAYS for o in plan.debit_orders)


def test_mixed_plan_adds_a_smaller_side_income(reference_data: ReferenceData) -> None:
    customer = customer_with(reference_data, IncomeBand.MIDDLE, IncomeSource.MIXED)
    plan = plan_recurring(rng(), customer, reference_data.accounts[0])
    assert 0.15 <= plan.side_income_cents / plan.monthly_income_cents <= 0.30


def test_business_plan_has_no_single_payer(reference_data: ReferenceData) -> None:
    customer = business_customer(reference_data)
    plan = plan_recurring(rng(), customer, reference_data.accounts[0])
    assert plan.payer_ref is None


def test_income_lands_once_on_payday_overnight() -> None:
    february = days(date(2026, 2, 1), 28)
    credits = [
        draft
        for day in february
        for draft in recurring_drafts(rng(), SALARY_PLAN, day)
        if draft.direction is Direction.CREDIT
    ]
    assert len(credits) == 1
    salary = credits[0]
    assert to_sast(salary.event_time).date() == PAYDAY
    assert salary.channel is Channel.SALARY_CREDIT
    assert salary.amount_cents == 2_500_000
    assert INCOME_WINDOW[0] <= sa_time(salary) < INCOME_WINDOW[1]


def test_debit_orders_run_on_their_day_after_pay_and_after_income() -> None:
    on_payday = recurring_drafts(rng(), SALARY_PLAN, PAYDAY)
    salary, insurance = on_payday
    assert insurance.counterparty_external_ref == INSURANCE.collector_ref
    assert DEBIT_ORDER_WINDOW[0] <= sa_time(insurance) < DEBIT_ORDER_WINDOW[1]
    assert salary.event_time < insurance.event_time

    two_days_later = recurring_drafts(rng(), SALARY_PLAN, PAYDAY + timedelta(days=2))
    assert [draft.amount_cents for draft in two_days_later] == [PHONE.amount_cents]


def test_nothing_is_scheduled_on_an_ordinary_day() -> None:
    assert recurring_drafts(rng(), SALARY_PLAN, date(2026, 2, 10)) == []


def test_mixed_earners_get_side_income_mid_month() -> None:
    plan = replace(
        SALARY_PLAN,
        income_source=IncomeSource.MIXED,
        side_income_cents=500_000,
        debit_orders=(),
    )
    side_day = PAYDAY + timedelta(days=SIDE_INCOME_DAYS_AFTER_PAY)
    (side_income,) = recurring_drafts(rng(), plan, side_day)
    assert side_income.channel is Channel.EFT
    assert side_income.direction is Direction.CREDIT
    assert side_income.amount_cents == 500_000


def test_business_receipts_add_up_to_turnover_over_a_year() -> None:
    plan = RecurringPlan(
        account_id="ACC-0000001",
        income_source=IncomeSource.BUSINESS,
        pay_day=28,
        monthly_income_cents=20_000_000,
        payer_ref=None,
        side_income_cents=0,
        debit_orders=(),
    )
    generator = rng()
    year = days(date(2026, 1, 1), 365)
    receipts = [d for day in year for d in recurring_drafts(generator, plan, day)]

    assert all(r.channel is Channel.EFT and r.direction is Direction.CREDIT for r in receipts)
    monthly_total = sum(r.amount_cents for r in receipts) / 12
    assert monthly_total == pytest.approx(20_000_000, rel=0.15)


@pytest.mark.parametrize("source", list(IncomeSource))
def test_every_draft_becomes_a_valid_transaction(
    reference_data: ReferenceData, source: IncomeSource
) -> None:
    if source is IncomeSource.BUSINESS:
        customer = business_customer(reference_data)
    else:
        customer = customer_with(reference_data, IncomeBand.MIDDLE, source)
    generator = rng()
    plan = plan_recurring(generator, customer, reference_data.accounts[0])

    quarter = days(date(2026, 1, 1), 90)
    drafts = [d for day in quarter for d in recurring_drafts(generator, plan, day)]
    assert drafts
    for number, draft in enumerate(drafts, start=1):
        to_transaction(draft, number, APPROVED)


def test_same_seed_gives_the_same_schedule() -> None:
    def schedule() -> list[TransactionDraft]:
        generator = rng()
        two_months = days(date(2026, 1, 1), 60)
        return [d for day in two_months for d in recurring_drafts(generator, SALARY_PLAN, day)]

    assert schedule() == schedule()
