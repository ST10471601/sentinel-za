"""Card-not-present fraud: stolen card details used online, mostly at foreign merchants.

Shape (SABRIC 2025, see SZ-3): one to three small test charges, then a burst of larger
charges within hours. About two-thirds of the losses are abroad.
"""

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import Card, CardType
from sentinel.domain.labels import FraudType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.simulator.card_activity import HOME_COUNTRY, online_purchase_draft
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.episodes import ScriptedEpisode, Victim
from sentinel.simulator.randomness import chance

FOREIGN_SHARE = 0.65  # 67.5% of credit and 63.5% of debit CNP losses are abroad
CREDIT_CARD_SHARE = 0.6  # credit cards lose slightly more to CNP, though fewer people have one
TEST_CHARGES = (1, 3)
TEST_AMOUNT_CENTS = (1_000, 10_000)  # R10 to R100
MAIN_CHARGES = (2, 8)
MAIN_AMOUNT_CENTS = (50_000, 3_000_000)  # R500 to R30,000
TEST_GAP_SECONDS = (60, 600)
MAIN_GAP_SECONDS = (300, 2_400)
MERCHANTS_PER_EPISODE = (1, 3)

# Where SABRIC says CNP losses land, by card type.
CATEGORIES_BY_CARD_TYPE: dict[CardType, tuple[MerchantCategory, ...]] = {
    CardType.CREDIT: (
        MerchantCategory.ADVERTISING,
        MerchantCategory.TRAVEL_AGENCY,
        MerchantCategory.BETTING,
    ),
    CardType.DEBIT: (
        MerchantCategory.DIGITAL_GOODS,
        MerchantCategory.ADVERTISING,
        MerchantCategory.SOFTWARE,
    ),
}


class CardNotPresent:
    """Plans card-not-present bursts against a victim's debit or credit card."""

    fraud_type = FraudType.CARD_NOT_PRESENT

    def __init__(self, merchants: Sequence[Merchant]) -> None:
        # Online merchants by (category, is foreign).
        self._online: dict[tuple[MerchantCategory, bool], list[Merchant]] = defaultdict(list)
        for merchant in merchants:
            if merchant.is_online:
                is_foreign = merchant.country_code != HOME_COUNTRY
                self._online[(merchant.category, is_foreign)].append(merchant)

    def can_target(self, victim: Victim) -> bool:
        """Every customer has a card whose details can be phished."""
        return bool(victim.cards)

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> ScriptedEpisode:
        """Plan the test charges and the burst that follows."""
        card = _pick_card(rng, victim.cards)
        is_foreign = chance(rng, FOREIGN_SHARE)
        merchants = self._pick_merchants(rng, card.card_type, is_foreign)
        test_count = rng.randint(*TEST_CHARGES)
        main_count = rng.randint(*MAIN_CHARGES)

        drafts: list[TransactionDraft] = []
        moment = start
        for number in range(test_count + main_count):
            is_test = number < test_count
            amount = _log_uniform_cents(rng, TEST_AMOUNT_CENTS if is_test else MAIN_AMOUNT_CENTS)
            drafts.append(online_purchase_draft(card, rng.choice(merchants), amount, moment))
            gap = TEST_GAP_SECONDS if is_test else MAIN_GAP_SECONDS
            moment += timedelta(seconds=rng.randint(*gap))

        return ScriptedEpisode(
            scenario_id=scenario_id,
            fraud_type=self.fraud_type,
            victim_customer_id=victim.customer.customer_id,
            params={
                "card_type": card.card_type.value,
                "foreign": is_foreign,
                "test_charges": test_count,
                "charges": main_count,
                "merchants": len(merchants),
            },
            drafts=tuple(drafts),
        )

    def _pick_merchants(
        self, rng: random.Random, card_type: CardType, is_foreign: bool
    ) -> list[Merchant]:
        """A few merchants in one fraud category, at home or abroad."""
        categories = [
            c for c in CATEGORIES_BY_CARD_TYPE[card_type] if self._online.get((c, is_foreign))
        ]
        if not categories:
            raise SimulationError(f"no online merchants for {card_type} card-not-present fraud")
        options = self._online[(rng.choice(categories), is_foreign)]
        count = min(rng.randint(*MERCHANTS_PER_EPISODE), len(options))
        return rng.sample(options, count)


def _pick_card(rng: random.Random, cards: Sequence[Card]) -> Card:
    credit = [card for card in cards if card.card_type is CardType.CREDIT]
    if credit and chance(rng, CREDIT_CARD_SHARE):
        return credit[0]
    for card in cards:
        if card.card_type is CardType.DEBIT:
            return card
    raise SimulationError("victim has no debit card")


def _log_uniform_cents(rng: random.Random, bounds: tuple[int, int]) -> int:
    """Small amounts are more common than large ones, but every size is possible."""
    low, high = bounds
    return round(math.exp(rng.uniform(math.log(low), math.log(high))))
