"""Everyday card use: purchases in store and online, and ATM withdrawals."""

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sentinel.domain.accounts import Card, CardType
from sentinel.domain.customers import Customer
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.domain.transactions import Channel, Direction, EntryMode, TransactionAuthMethod
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.randomness import chance, pick
from sentinel.simulator.spending import (
    SPENDING_BY_BAND,
    atm_amount,
    card_amount,
    pick_event_time,
    sample_daily_count,
)

HOME_COUNTRY = "ZA"
FAVOURITES_PER_CATEGORY = (1, 3)
NEAREST_MERCHANT_COUNT = 3  # used when the home city has none of a category
NEW_MERCHANT_CHANCE = 0.1  # most purchases are at a regular shop
CREDIT_CARD_USE_SHARE = 0.4  # of purchases, for customers with a credit card

# Foreign online merchants customers really use: streaming, software, ads, shopping, travel.
# Foreign betting and crypto merchants are left to the fraud scenarios.
LEGIT_FOREIGN_CATEGORIES = frozenset(
    {
        MerchantCategory.DIGITAL_GOODS,
        MerchantCategory.SOFTWARE,
        MerchantCategory.ADVERTISING,
        MerchantCategory.ONLINE_RETAIL,
        MerchantCategory.TRAVEL_AGENCY,
    }
)

# Simulation assumptions: tap-to-pay needs no PIN up to R500, and fixed exchange rates.
CONTACTLESS_NO_PIN_LIMIT_CENTS = 50_000
CURRENCY_BY_COUNTRY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "NL": "EUR", "IE": "EUR", "MT": "EUR",
    "CY": "EUR", "SG": "SGD", "HK": "HKD", "CA": "CAD",
}  # fmt: skip
ZAR_PER_UNIT: dict[str, float] = {
    "USD": 18.0, "GBP": 23.5, "EUR": 20.5, "SGD": 13.5, "HKD": 2.3, "CAD": 13.0,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class CardHabits:
    """Which cards a customer carries and where they usually use them."""

    debit_card: Card
    credit_card: Card | None
    options: Mapping[MerchantCategory, tuple[Merchant, ...]]  # every merchant they'd use
    favourites: Mapping[MerchantCategory, tuple[Merchant, ...]]  # the ones they return to


class MerchantIndex:
    """Merchants grouped by category, and physical ones also by city."""

    def __init__(self, merchants: Sequence[Merchant]) -> None:
        self._by_city: dict[tuple[str, MerchantCategory], list[Merchant]] = defaultdict(list)
        self._physical: dict[MerchantCategory, list[Merchant]] = defaultdict(list)
        self._online: dict[MerchantCategory, list[Merchant]] = defaultdict(list)

        for merchant in merchants:
            if merchant.is_online:
                if merchant.country_code == HOME_COUNTRY or (
                    merchant.category in LEGIT_FOREIGN_CATEGORIES
                ):
                    self._online[merchant.category].append(merchant)
            else:
                self._physical[merchant.category].append(merchant)
                self._by_city[(str(merchant.city), merchant.category)].append(merchant)

    def options_for(self, customer: Customer, category: MerchantCategory) -> list[Merchant]:
        """Merchants the customer would use: local shops plus online ones.

        With neither, the nearest few shops of that category elsewhere.
        """
        options = self._by_city[(customer.home_city, category)] + self._online[category]
        if options:
            return options
        return sorted(
            self._physical[category], key=lambda merchant: _distance_score(merchant, customer)
        )[:NEAREST_MERCHANT_COUNT]


def plan_card_habits(
    rng: random.Random, customer: Customer, cards: Sequence[Card], merchants: MerchantIndex
) -> CardHabits:
    """Fix a customer's cards and favourite merchants for the simulation."""
    categories = {*SPENDING_BY_BAND[customer.income_band].category_weights, MerchantCategory.ATM}
    options: dict[MerchantCategory, tuple[Merchant, ...]] = {}
    favourites: dict[MerchantCategory, tuple[Merchant, ...]] = {}

    for category in sorted(categories):  # sorted, so the random draws are reproducible
        options[category] = tuple(merchants.options_for(customer, category))
        count = min(rng.randint(*FAVOURITES_PER_CATEGORY), len(options[category]))
        favourites[category] = tuple(rng.sample(options[category], count))

    return CardHabits(
        debit_card=_card_of_type(cards, CardType.DEBIT),
        credit_card=next((card for card in cards if card.card_type is CardType.CREDIT), None),
        options=options,
        favourites=favourites,
    )


def card_drafts(
    rng: random.Random,
    customer: Customer,
    habits: CardHabits,
    day: date,
    weight: float,
    budget: DayBudget,
) -> list[TransactionDraft]:
    """Return the customer's card purchases and ATM withdrawals for one day."""
    profile = SPENDING_BY_BAND[customer.income_band]
    drafts: list[TransactionDraft] = []

    for _ in range(sample_daily_count(rng, profile.card_purchases_per_month, weight)):
        category = pick(rng, profile.category_weights)
        merchant = _pick_merchant(rng, habits, category)
        card = _pick_card(rng, habits)
        amount = card_amount(rng, category, customer.income_band)
        if budget.try_spend(card.account_id, amount):
            drafts.append(_purchase(rng, card, merchant, amount, day, profile.contactless_share))

    for _ in range(sample_daily_count(rng, profile.atm_withdrawals_per_month, weight)):
        atm = _pick_merchant(rng, habits, MerchantCategory.ATM)
        amount = atm_amount(rng, customer.income_band)
        if budget.try_spend(habits.debit_card.account_id, amount):
            drafts.append(_withdrawal(rng, habits.debit_card, atm, amount, day))

    return drafts


def _purchase(
    rng: random.Random,
    card: Card,
    merchant: Merchant,
    amount_cents: int,
    day: date,
    contactless_share: float,
) -> TransactionDraft:
    event_time = pick_event_time(rng, day)
    if merchant.is_online:
        currency = CURRENCY_BY_COUNTRY.get(merchant.country_code)
        return TransactionDraft(
            account_id=card.account_id,
            direction=Direction.DEBIT,
            amount_cents=amount_cents,
            event_time=event_time,
            channel=Channel.CARD_NOT_PRESENT,
            auth_method=TransactionAuthMethod.THREE_DS,
            country_code=merchant.country_code,
            original_amount_cents=(
                round(amount_cents / ZAR_PER_UNIT[currency]) if currency else None
            ),
            original_currency=currency,
            card_id=card.card_id,
            merchant_id=merchant.merchant_id,
            entry_mode=EntryMode.ECOMMERCE,
        )

    is_contactless = chance(rng, contactless_share)
    needs_pin = not is_contactless or amount_cents > CONTACTLESS_NO_PIN_LIMIT_CENTS
    return TransactionDraft(
        account_id=card.account_id,
        direction=Direction.DEBIT,
        amount_cents=amount_cents,
        event_time=event_time,
        channel=Channel.CARD_PRESENT,
        auth_method=TransactionAuthMethod.PIN if needs_pin else TransactionAuthMethod.NONE,
        country_code=HOME_COUNTRY,
        card_id=card.card_id,
        merchant_id=merchant.merchant_id,
        entry_mode=EntryMode.CONTACTLESS if is_contactless else EntryMode.CHIP,
        terminal_lat=merchant.lat,
        terminal_lon=merchant.lon,
    )


def _withdrawal(
    rng: random.Random, card: Card, atm: Merchant, amount_cents: int, day: date
) -> TransactionDraft:
    return TransactionDraft(
        account_id=card.account_id,
        direction=Direction.DEBIT,
        amount_cents=amount_cents,
        event_time=pick_event_time(rng, day),
        channel=Channel.ATM,
        auth_method=TransactionAuthMethod.PIN,
        country_code=HOME_COUNTRY,
        card_id=card.card_id,
        merchant_id=atm.merchant_id,
        entry_mode=EntryMode.CHIP,
        terminal_lat=atm.lat,
        terminal_lon=atm.lon,
    )


def _pick_merchant(rng: random.Random, habits: CardHabits, category: MerchantCategory) -> Merchant:
    if chance(rng, NEW_MERCHANT_CHANCE):
        return rng.choice(habits.options[category])
    return rng.choice(habits.favourites[category])


def _pick_card(rng: random.Random, habits: CardHabits) -> Card:
    if habits.credit_card is not None and chance(rng, CREDIT_CARD_USE_SHARE):
        return habits.credit_card
    return habits.debit_card


def _card_of_type(cards: Sequence[Card], card_type: CardType) -> Card:
    for card in cards:
        if card.card_type is card_type:
            return card
    raise ValueError(f"customer has no {card_type} card")


def _distance_score(merchant: Merchant, customer: Customer) -> float:
    """Squared distance in degrees: fine for ranking nearby places."""
    # Only physical merchants are ranked, and they always have coordinates.
    lat, lon = merchant.lat or 0.0, merchant.lon or 0.0
    return (lat - customer.home_lat) ** 2 + (lon - customer.home_lon) ** 2
