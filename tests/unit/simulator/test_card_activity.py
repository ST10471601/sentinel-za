import random
from collections import defaultdict
from datetime import date, timedelta

import pytest

from sentinel.domain.accounts import Card, CardType
from sentinel.domain.customers import Customer, IncomeBand
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.domain.transactions import (
    Channel,
    EntryMode,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.budget import DayBudget
from sentinel.simulator.card_activity import (
    CONTACTLESS_NO_PIN_LIMIT_CENTS,
    NEAREST_MERCHANT_COUNT,
    ZAR_PER_UNIT,
    CardHabits,
    MerchantIndex,
    card_drafts,
    plan_card_habits,
)
from sentinel.simulator.drafts import Settlement, TransactionDraft, to_transaction
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.spending import SPENDING_BY_BAND

START = date(2026, 3, 2)
RICH = 10**12  # a budget nobody runs out of
APPROVED = Settlement(TransactionStatus.APPROVED, None, 0)


@pytest.fixture(scope="module")
def merchant_by_id(reference_data: ReferenceData) -> dict[str, Merchant]:
    return {merchant.merchant_id: merchant for merchant in reference_data.merchants}


@pytest.fixture(scope="module")
def sample(
    reference_data: ReferenceData,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
) -> list[tuple[Customer, CardHabits]]:
    generator = random.Random(4)
    return [
        (
            customer,
            plan_card_habits(generator, customer, cards_by_customer[customer.customer_id], index),
        )
        for customer in reference_data.customers[:120]
    ]


def month_of_drafts(
    sample: list[tuple[Customer, CardHabits]], budget_cents: int = RICH, seed: int = 8
) -> list[TransactionDraft]:
    generator = random.Random(seed)
    drafts: list[TransactionDraft] = []
    for customer, habits in sample:
        accounts = {habits.debit_card.account_id}
        if habits.credit_card:
            accounts.add(habits.credit_card.account_id)
        for offset in range(30):
            budget = DayBudget(generator, dict.fromkeys(accounts, budget_cents))
            day = START + timedelta(days=offset)
            drafts += card_drafts(generator, customer, habits, day, 1.0, budget)
    return drafts


@pytest.fixture(scope="module")
def drafts(sample: list[tuple[Customer, CardHabits]]) -> list[TransactionDraft]:
    return month_of_drafts(sample)


def test_habits_cover_every_spending_category_plus_atms(
    sample: list[tuple[Customer, CardHabits]],
) -> None:
    for customer, habits in sample:
        expected = {*SPENDING_BY_BAND[customer.income_band].category_weights, MerchantCategory.ATM}
        assert set(habits.favourites) == expected
        for category, favourites in habits.favourites.items():
            assert favourites and set(favourites) <= set(habits.options[category])


def test_shops_come_from_the_home_city(sample: list[tuple[Customer, CardHabits]]) -> None:
    for customer, habits in sample:
        for merchant in habits.options[MerchantCategory.GROCERY]:
            assert merchant.city == customer.home_city


def test_without_a_local_shop_the_nearest_ones_are_used(
    reference_data: ReferenceData, index: MerchantIndex
) -> None:
    toll_cities = {m.city for m in reference_data.merchants if m.category is MerchantCategory.TOLL}
    customer = next(c for c in reference_data.customers if c.home_city not in toll_cities)

    def distance(merchant: Merchant) -> float:
        lat, lon = merchant.lat or 0.0, merchant.lon or 0.0
        return (lat - customer.home_lat) ** 2 + (lon - customer.home_lon) ** 2

    options = index.options_for(customer, MerchantCategory.TOLL)
    plazas = [m for m in reference_data.merchants if m.category is MerchantCategory.TOLL]
    assert len(options) == NEAREST_MERCHANT_COUNT
    assert sorted(map(distance, options)) == sorted(map(distance, plazas))[:NEAREST_MERCHANT_COUNT]
    assert all(m.city != customer.home_city for m in options)


def test_online_options_skip_foreign_betting_and_crypto(
    reference_data: ReferenceData, index: MerchantIndex
) -> None:
    customer = reference_data.customers[0]
    for category in (MerchantCategory.BETTING, MerchantCategory.CRYPTO):
        online = [m for m in index.options_for(customer, category) if m.is_online]
        assert online and all(m.country_code == "ZA" for m in online)


def test_every_draft_becomes_a_valid_transaction(drafts: list[TransactionDraft]) -> None:
    channels = {draft.channel for draft in drafts}
    assert channels == {Channel.CARD_PRESENT, Channel.CARD_NOT_PRESENT, Channel.ATM}
    for number, draft in enumerate(drafts, start=1):
        to_transaction(draft, number, APPROVED)


def test_in_store_purchases_happen_at_the_shop_with_pin_rules(
    drafts: list[TransactionDraft], merchant_by_id: dict[str, Merchant]
) -> None:
    in_store = [d for d in drafts if d.channel is Channel.CARD_PRESENT]
    for draft in in_store:
        assert draft.merchant_id is not None
        merchant = merchant_by_id[draft.merchant_id]
        assert (draft.terminal_lat, draft.terminal_lon) == (merchant.lat, merchant.lon)
        pin_free = draft.auth_method is TransactionAuthMethod.NONE
        if pin_free:
            assert draft.entry_mode is EntryMode.CONTACTLESS
            assert draft.amount_cents <= CONTACTLESS_NO_PIN_LIMIT_CENTS


def test_foreign_online_purchases_keep_the_original_currency(
    drafts: list[TransactionDraft],
) -> None:
    foreign = [d for d in drafts if d.country_code != "ZA"]
    assert foreign
    for draft in foreign:
        assert draft.channel is Channel.CARD_NOT_PRESENT
        assert draft.original_currency is not None and draft.original_amount_cents is not None
        rate = ZAR_PER_UNIT[draft.original_currency]
        assert draft.original_amount_cents == round(draft.amount_cents / rate)


def test_atm_withdrawals_use_the_debit_card_and_a_pin(
    drafts: list[TransactionDraft], reference_data: ReferenceData
) -> None:
    card_type = {card.card_id: card.card_type for card in reference_data.cards}
    withdrawals = [d for d in drafts if d.channel is Channel.ATM]
    assert withdrawals
    for draft in withdrawals:
        assert draft.card_id is not None and card_type[draft.card_id] is CardType.DEBIT
        assert draft.auth_method is TransactionAuthMethod.PIN
        assert draft.amount_cents % 5_000 == 0


def test_most_purchases_are_at_favourite_merchants(
    sample: list[tuple[Customer, CardHabits]], drafts: list[TransactionDraft]
) -> None:
    favourite_ids = {
        habits.debit_card.account_id: {
            m.merchant_id for ms in habits.favourites.values() for m in ms
        }
        for _, habits in sample
    }
    debit_drafts = [d for d in drafts if d.account_id in favourite_ids]
    at_favourite = sum(d.merchant_id in favourite_ids[d.account_id] for d in debit_drafts)
    assert at_favourite / len(debit_drafts) > 0.85


def test_purchase_counts_follow_the_income_band(
    sample: list[tuple[Customer, CardHabits]], drafts: list[TransactionDraft]
) -> None:
    band_by_account: dict[str, IncomeBand] = {}
    for customer, habits in sample:
        band_by_account[habits.debit_card.account_id] = customer.income_band
        if habits.credit_card:
            band_by_account[habits.credit_card.account_id] = customer.income_band
    per_band: dict[IncomeBand, int] = defaultdict(int)
    customers_per_band: dict[IncomeBand, int] = defaultdict(int)
    for customer, _ in sample:
        customers_per_band[customer.income_band] += 1
    for draft in drafts:
        if draft.channel is not Channel.ATM:
            per_band[band_by_account[draft.account_id]] += 1

    low = per_band[IncomeBand.LOW] / customers_per_band[IncomeBand.LOW]
    high = per_band[IncomeBand.HIGH] / customers_per_band[IncomeBand.HIGH]
    assert low < high


def test_an_empty_budget_stops_most_spending(sample: list[tuple[Customer, CardHabits]]) -> None:
    broke = month_of_drafts(sample, budget_cents=0)
    assert len(broke) < 0.2 * len(month_of_drafts(sample))


def test_customer_without_a_debit_card_is_a_data_error(
    reference_data: ReferenceData,
    index: MerchantIndex,
    cards_by_customer: dict[str, list[Card]],
) -> None:
    credit_cards = [card for card in reference_data.cards if card.card_type is CardType.CREDIT]
    owner = next(
        customer
        for customer in reference_data.customers
        if credit_cards[0] in cards_by_customer[customer.customer_id]
    )
    with pytest.raises(ValueError, match="no debit card"):
        plan_card_habits(random.Random(1), owner, credit_cards[:1], index)


def test_same_seed_gives_the_same_activity(sample: list[tuple[Customer, CardHabits]]) -> None:
    assert month_of_drafts(sample[:10], seed=3) == month_of_drafts(sample[:10], seed=3)
