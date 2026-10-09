"""Generate accounts for each customer, and the cards linked to them."""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sentinel.core.datetimes import add_years
from sentinel.domain.accounts import Account, AccountStatus, AccountType, Card, CardStatus, CardType
from sentinel.domain.customers import Customer, CustomerType, IncomeBand
from sentinel.simulator.identifiers import (
    make_account_number,
    make_card_token,
    make_id,
    make_pan_last4,
)
from sentinel.simulator.randomness import chance, draw_unique, random_datetime_between

DEFAULT_PAYSHAP_DAILY_LIMIT_CENTS = 5_000_000  # R50,000, a common bank default

BALANCE_SPREAD = 1.0  # log-normal sigma: a few customers hold far more than the median
MAX_CREDIT_USED = 0.6  # opening amount owed, as a share of the credit limit
CREDIT_LIMIT_STEP_CENTS = 50_000  # limits are whole R500 amounts
DORMANT_SAVINGS_SHARE = 0.15  # quiet accounts that wake up are a mule signal, so have some

CARD_VALIDITY_YEARS = 4
MAX_CARD_AGE_DAYS = 3 * 365  # cards are reissued, so none is older than this

SAVINGS_CHANCE: dict[IncomeBand, float] = {
    IncomeBand.LOW: 0.20,
    IncomeBand.MIDDLE: 0.45,
    IncomeBand.HIGH: 0.70,
    IncomeBand.BUSINESS: 0.0,
}
CREDIT_CARD_CHANCE: dict[IncomeBand, float] = {
    IncomeBand.LOW: 0.05,
    IncomeBand.MIDDLE: 0.30,
    IncomeBand.HIGH: 0.70,
    IncomeBand.BUSINESS: 0.30,
}

CARD_TYPE_BY_ACCOUNT_TYPE: dict[AccountType, CardType] = {
    AccountType.CHEQUE: CardType.DEBIT,
    AccountType.BUSINESS_CURRENT: CardType.DEBIT,
    AccountType.CREDIT_CARD: CardType.CREDIT,
}


@dataclass(frozen=True, slots=True)
class BandProfile:
    """Typical balances and limits for one income band, in cents."""

    median_current_balance: int
    median_savings_balance: int
    min_credit_limit: int
    max_credit_limit: int
    daily_transfer_limit: int
    daily_atm_limit: int
    daily_pos_limit: int


PROFILE_BY_BAND: dict[IncomeBand, BandProfile] = {
    IncomeBand.LOW: BandProfile(
        median_current_balance=80_000,
        median_savings_balance=150_000,
        min_credit_limit=300_000,
        max_credit_limit=1_000_000,
        daily_transfer_limit=1_000_000,
        daily_atm_limit=300_000,
        daily_pos_limit=1_000_000,
    ),
    IncomeBand.MIDDLE: BandProfile(
        median_current_balance=600_000,
        median_savings_balance=2_000_000,
        min_credit_limit=1_000_000,
        max_credit_limit=5_000_000,
        daily_transfer_limit=5_000_000,
        daily_atm_limit=500_000,
        daily_pos_limit=3_000_000,
    ),
    IncomeBand.HIGH: BandProfile(
        median_current_balance=3_500_000,
        median_savings_balance=15_000_000,
        min_credit_limit=5_000_000,
        max_credit_limit=25_000_000,
        daily_transfer_limit=15_000_000,
        daily_atm_limit=1_000_000,
        daily_pos_limit=10_000_000,
    ),
    IncomeBand.BUSINESS: BandProfile(
        median_current_balance=12_000_000,
        median_savings_balance=30_000_000,
        min_credit_limit=5_000_000,
        max_credit_limit=50_000_000,
        daily_transfer_limit=100_000_000,
        daily_atm_limit=1_000_000,
        daily_pos_limit=10_000_000,
    ),
}


def generate_accounts(
    rng: random.Random, customers: Sequence[Customer], simulation_start: datetime
) -> list[Account]:
    """Give each customer a main account, plus savings and credit-card accounts by chance."""
    used_numbers: set[str] = set()
    accounts: list[Account] = []

    for customer in customers:
        for position, account_type in enumerate(_pick_account_types(rng, customer)):
            # The main account opens on the day the customer joins; others open later.
            opened_at = (
                customer.onboarded_at
                if position == 0
                else random_datetime_between(rng, customer.onboarded_at, simulation_start)
            )
            account = _make_account(
                rng,
                account_id=make_id("ACC", len(accounts) + 1),
                customer=customer,
                account_type=account_type,
                opened_at=opened_at,
                account_number=draw_unique(lambda: make_account_number(rng), used_numbers),
            )
            accounts.append(account)
    return accounts


def generate_cards(
    rng: random.Random,
    accounts: Sequence[Account],
    customers: Sequence[Customer],
    simulation_start: datetime,
) -> list[Card]:
    """Issue a debit card for each current account and a credit card for each credit account."""
    band_by_customer = {customer.customer_id: customer.income_band for customer in customers}
    oldest_issue = simulation_start - timedelta(days=MAX_CARD_AGE_DAYS)
    cards: list[Card] = []

    for account in accounts:
        card_type = CARD_TYPE_BY_ACCOUNT_TYPE.get(account.account_type)
        if card_type is None:
            continue  # savings accounts have no card

        profile = PROFILE_BY_BAND[band_by_customer[account.customer_id]]
        issued_at = random_datetime_between(
            rng, max(account.opened_at, oldest_issue), simulation_start
        )
        cards.append(
            Card(
                card_id=make_id("CRD", len(cards) + 1),
                account_id=account.account_id,
                card_type=card_type,
                pan_token=make_card_token(rng),
                pan_last4=make_pan_last4(rng),
                issued_at=issued_at,
                expires_on=add_years(issued_at.date(), CARD_VALIDITY_YEARS),
                status=CardStatus.ACTIVE,
                daily_atm_limit_cents=profile.daily_atm_limit,
                daily_pos_limit_cents=profile.daily_pos_limit,
            )
        )
    return cards


def _pick_account_types(rng: random.Random, customer: Customer) -> list[AccountType]:
    """Main account first, then any extra accounts."""
    if customer.customer_type is CustomerType.BUSINESS:
        account_types = [AccountType.BUSINESS_CURRENT]
    else:
        account_types = [AccountType.CHEQUE]

    if chance(rng, SAVINGS_CHANCE[customer.income_band]):
        account_types.append(AccountType.SAVINGS)
    if chance(rng, CREDIT_CARD_CHANCE[customer.income_band]):
        account_types.append(AccountType.CREDIT_CARD)
    return account_types


def _make_account(
    rng: random.Random,
    *,
    account_id: str,
    customer: Customer,
    account_type: AccountType,
    opened_at: datetime,
    account_number: str,
) -> Account:
    profile = PROFILE_BY_BAND[customer.income_band]
    credit_limit = None
    if account_type is AccountType.CREDIT_CARD:
        credit_limit = rng.randrange(
            profile.min_credit_limit, profile.max_credit_limit + 1, CREDIT_LIMIT_STEP_CENTS
        )

    is_dormant = account_type is AccountType.SAVINGS and chance(rng, DORMANT_SAVINGS_SHARE)
    return Account(
        account_id=account_id,
        customer_id=customer.customer_id,
        account_number=account_number,
        account_type=account_type,
        opened_at=opened_at,
        status=AccountStatus.DORMANT if is_dormant else AccountStatus.ACTIVE,
        opening_balance_cents=_pick_opening_balance(rng, account_type, profile, credit_limit),
        credit_limit_cents=credit_limit,
        daily_transfer_limit_cents=profile.daily_transfer_limit,
        payshap_daily_limit_cents=DEFAULT_PAYSHAP_DAILY_LIMIT_CENTS,
    )


def _pick_opening_balance(
    rng: random.Random,
    account_type: AccountType,
    profile: BandProfile,
    credit_limit: int | None,
) -> int:
    if credit_limit is not None:
        return int(credit_limit * rng.uniform(0, MAX_CREDIT_USED))  # amount owed
    if account_type is AccountType.SAVINGS:
        median = profile.median_savings_balance
    else:
        median = profile.median_current_balance
    return int(rng.lognormvariate(math.log(median), BALANCE_SPREAD))
