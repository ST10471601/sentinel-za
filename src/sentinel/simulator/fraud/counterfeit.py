"""Counterfeit cards: the card and PIN are skimmed, then a clone is used in another city.

SABRIC 2025 gives the losses (debit R13.6m, credit R4.4m) but no description, so the shape is
the standard pattern and a simulation assumption: the card is skimmed while the victim uses it,
then 3 to 30 days later a magstripe clone and the PIN take cash and goods far from home. The
victim still has the real card and keeps using it, so the same card is in two places at once.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import Card, CardType
from sentinel.domain.customers import Customer
from sentinel.domain.labels import FraudType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.simulator.card_activity import (
    MerchantIndex,
    atm_withdrawal_draft,
    store_purchase_draft,
    swiped,
)
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.cash_out import DRIVE_SECONDS, Round, take_cash
from sentinel.simulator.fraud.episodes import (
    Balances,
    FraudAction,
    FraudActions,
    ParamValue,
    Victim,
)
from sentinel.simulator.geography import CITIES, City
from sentinel.simulator.randomness import chance, pick, random_datetime_between
from sentinel.simulator.spending import atm_amount, card_amount, pick_event_time
from sentinel.simulator.unusual_events import away_customer

DEBIT_CARD_SHARE = 0.76  # debit losses R13.6m vs credit R4.4m

# Simulation assumptions from here on.
NEARBY_MERCHANTS = 6
# Customers here never draw cash on a credit card, so an ATM skim of one would be a fraud-only
# signal. Credit cards are skimmed at a shop's card machine, where the card leaves sight.
CREDIT_SKIM_CATEGORIES = (MerchantCategory.FUEL, MerchantCategory.RESTAURANT)
FIRST_USE_DAYS = (3, 30)  # after the skim, once the clone is made or the details are sold
DAYS_USED = (1, 3)  # days in a row
ROUND_HOURS = (time(8), time(20))  # each day's round starts in shop hours and ends at midnight
PURCHASES = (1, 4)
# Goods that are easy to use or resell.
PURCHASE_CENTS: dict[MerchantCategory, tuple[int, int]] = {
    MerchantCategory.FUEL: (30_000, 150_000),  # up to a full tank
    MerchantCategory.CLOTHING: (50_000, 500_000),
    MerchantCategory.GROCERY: (20_000, 250_000),
}


@dataclass(frozen=True, slots=True)
class CounterfeitEpisode:
    """A cloned card used for a day or a few far from home, while the victim keeps the real one."""

    scenario_id: str
    victim_customer_id: str
    params: Mapping[str, ParamValue]
    card: Card
    skim: TransactionDraft  # the victim's own transaction where the card was skimmed, not fraud
    rounds: tuple[Round, ...]
    atms: tuple[Merchant, ...]
    shops: tuple[Merchant, ...]
    seed: int  # each round draws from its own stream, so the order days are played can't matter

    fraud_type = FraudType.CARD_PRESENT_COUNTERFEIT

    @property
    def card_id(self) -> str:
        """The skimmed card, blocked when the fraud is reported."""
        return self.card.card_id

    @property
    def card_loss(self) -> None:
        """The victim still has the real card."""
        return None

    def days(self) -> frozenset[date]:
        """SA calendar days of the skim and of each round."""
        times = [self.skim.event_time, *(spree.start for spree in self.rounds)]
        return frozenset(_sa_day(moment) for moment in times)

    def planned_end(self) -> datetime:
        """Midnight after the last day the clone is used."""
        return self.rounds[-1].end

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The victim's skimmed transaction, or the clone's round, on ``day``."""
        actions = FraudActions()
        if _sa_day(self.skim.event_time) == day:
            actions.genuine.append(self.skim)
        for spree in self.rounds:
            if _sa_day(spree.start) == day:
                available = balances.available_cents(self.card.account_id)
                actions.attempts += self._play(spree, available)
        return actions

    def _play(self, spree: Round, available_cents: int) -> list[FraudAction]:
        """Cash first, then shopping, all with the swiped clone and the skimmed PIN."""
        rng = random.Random(f"{self.seed}:{spree.start.isoformat()}")
        attempts, moment = take_cash(rng, self.card, self.atms, spree, available_cents)
        for _ in range(rng.randint(*PURCHASES)):
            moment += _seconds(rng, DRIVE_SECONDS)
            if moment >= spree.end:
                break
            shop = rng.choice(self.shops)
            amount = rng.randint(*PURCHASE_CENTS[shop.category])
            attempts.append(store_purchase_draft(self.card, shop, amount, moment, False))
        return [swiped(a) if isinstance(a, TransactionDraft) else a for a in attempts]


class Counterfeit:
    """Plans counterfeit card episodes: the skim, then the clone used in another city."""

    fraud_type = FraudType.CARD_PRESENT_COUNTERFEIT

    def __init__(self, merchants: Sequence[Merchant]) -> None:
        self._index = MerchantIndex(merchants)

    def can_target(self, victim: Victim) -> bool:
        """Every customer carries a debit card."""
        return any(card.card_type is CardType.DEBIT for card in victim.cards)

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> CounterfeitEpisode:
        """Plan the skim on ``start``'s day and the days the clone is used after it."""
        customer = victim.customer
        card = _pick_card(rng, victim.cards)
        skim_point = self._skim_point(rng, customer, card)
        skimmed_at = pick_event_time(rng, _sa_day(start))
        skim = _victim_transaction(rng, customer, card, skim_point, skimmed_at)

        first_use_after = rng.randint(*FIRST_USE_DAYS)
        first_day = _sa_day(skimmed_at) + timedelta(days=first_use_after)
        rounds = _plan_rounds(rng, first_day, rng.randint(*DAYS_USED))
        city = _cash_out_city(rng, customer.home_city)
        away = away_customer(customer, city)

        return CounterfeitEpisode(
            scenario_id=scenario_id,
            victim_customer_id=customer.customer_id,
            params={
                "card_type": card.card_type.value,
                "skimmed_at": skimmed_at.isoformat(),
                "skim_merchant_id": skim_point.merchant_id,
                "cash_out_city": city.name,
                "first_use_after_days": first_use_after,
                "days_used": len(rounds),
            },
            card=card,
            skim=skim,
            rounds=rounds,
            atms=self._nearby(away, MerchantCategory.ATM),
            shops=tuple(shop for c in PURCHASE_CENTS for shop in self._nearby(away, c)),
            seed=rng.getrandbits(32),
        )

    def _skim_point(self, rng: random.Random, customer: Customer, card: Card) -> Merchant:
        """An ATM near home for a debit card; a fuel station or restaurant for a credit card."""
        if card.card_type is CardType.DEBIT:
            category = MerchantCategory.ATM
        else:
            category = rng.choice(CREDIT_SKIM_CATEGORIES)
        return rng.choice(self._nearby(customer, category))

    def _nearby(self, customer: Customer, category: MerchantCategory) -> tuple[Merchant, ...]:
        nearby = self._index.nearest(customer, category, NEARBY_MERCHANTS)
        if not nearby:
            raise SimulationError(f"no {category} merchants for counterfeit card fraud")
        return tuple(nearby)


def _pick_card(rng: random.Random, cards: Sequence[Card]) -> Card:
    debit = next(card for card in cards if card.card_type is CardType.DEBIT)
    credit = next((card for card in cards if card.card_type is CardType.CREDIT), None)
    if credit is None or chance(rng, DEBIT_CARD_SHARE):
        return debit
    return credit


def _victim_transaction(
    rng: random.Random, customer: Customer, card: Card, merchant: Merchant, moment: datetime
) -> TransactionDraft:
    """The victim's own withdrawal or purchase, with chip and PIN, as the card is skimmed."""
    band = customer.income_band
    if merchant.category is MerchantCategory.ATM:
        return atm_withdrawal_draft(card, merchant, atm_amount(rng, band), moment)
    amount = card_amount(rng, merchant.category, band)
    return store_purchase_draft(card, merchant, amount, moment, False)


def _plan_rounds(rng: random.Random, first_day: date, days_used: int) -> tuple[Round, ...]:
    """One round on each day of use, from a daytime start until midnight."""
    rounds: list[Round] = []
    for offset in range(days_used):
        day = first_day + timedelta(days=offset)
        opens = datetime.combine(day, ROUND_HOURS[0], tzinfo=SAST)
        closes = datetime.combine(day, ROUND_HOURS[1], tzinfo=SAST)
        midnight = datetime.combine(day + timedelta(days=1), time(), tzinfo=SAST)
        start = random_datetime_between(rng, opens, closes)
        rounds.append(Round(start.astimezone(UTC), midnight.astimezone(UTC)))
    return tuple(rounds)


def _cash_out_city(rng: random.Random, home_city: str) -> City:
    """Another city, more often a big one where strangers go unnoticed."""
    return pick(rng, {city: city.weight for city in CITIES if city.name != home_city})


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
