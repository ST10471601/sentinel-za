"""Generate merchants: shops and ATMs in each city, plus local and foreign online merchants.

Names are invented. The same name in several cities is a chain, as in real data.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass

from sentinel.domain.merchants import MCCS_BY_CATEGORY, Merchant, MerchantCategory
from sentinel.simulator.geography import CITIES, City, jitter_location
from sentinel.simulator.identifiers import make_id
from sentinel.simulator.names import BUSINESS_NAME_WORDS
from sentinel.simulator.randomness import pick

HOME_COUNTRY = "ZA"
MERCHANT_JITTER_DEGREES = 0.08  # about 8 km around the city centre

# Physical merchants per unit of city weight (a weight of 1.0 is about 1% of customers).
PHYSICAL_MERCHANTS_PER_WEIGHT: dict[MerchantCategory, float] = {
    MerchantCategory.GROCERY: 2.0,
    MerchantCategory.FUEL: 1.5,
    MerchantCategory.ATM: 3.0,
    MerchantCategory.RESTAURANT: 2.0,
    MerchantCategory.CLOTHING: 1.5,
    MerchantCategory.BETTING: 0.3,
}
# Every city, however small, has at least one of these.
ESSENTIAL_CATEGORIES = frozenset(
    {MerchantCategory.GROCERY, MerchantCategory.FUEL, MerchantCategory.ATM}
)

# Toll plazas on national roads, placed at the nearest city in the list.
TOLL_PLAZA_CITIES = (
    "Midrand", "Pretoria", "Pietermaritzburg", "Vereeniging",
    "Mbombela", "eMalahleni", "Rustenburg", "Paarl",
)  # fmt: skip

DOMESTIC_ONLINE_COUNTS: dict[MerchantCategory, int] = {
    MerchantCategory.ONLINE_RETAIL: 20,
    MerchantCategory.DIGITAL_GOODS: 10,
    MerchantCategory.AIRTIME: 8,
    MerchantCategory.BETTING: 8,
    MerchantCategory.SOFTWARE: 6,
    MerchantCategory.TRAVEL_AGENCY: 6,
    MerchantCategory.CRYPTO: 4,
}

# Foreign card-not-present fraud concentrates in these categories (SABRIC 2025).
FOREIGN_ONLINE_COUNTS: dict[MerchantCategory, int] = {
    MerchantCategory.ADVERTISING: 15,
    MerchantCategory.DIGITAL_GOODS: 15,
    MerchantCategory.TRAVEL_AGENCY: 12,
    MerchantCategory.BETTING: 12,
    MerchantCategory.SOFTWARE: 12,
    MerchantCategory.ONLINE_RETAIL: 10,
    MerchantCategory.CRYPTO: 6,
}

# Simulation assumption: where foreign online merchants are registered.
FOREIGN_COUNTRY_WEIGHTS: dict[str, float] = {
    "US": 30, "GB": 20, "NL": 10, "IE": 10, "MT": 10,
    "CY": 5, "SG": 5, "HK": 5, "CA": 5,
}  # fmt: skip

ONLINE_NAME_WORDS: tuple[str, ...] = (
    "Bluefin", "Brightline", "Cloudpeak", "Driftwood", "Ember", "Foxglove",
    "Greystone", "Halcyon", "Ironbark", "Juniper", "Larkspur", "Mistral",
    "Northwind", "Oakridge", "Pinecrest", "Redwood", "Saltmarsh", "Tidewater",
)  # fmt: skip

NAME_ENDINGS: dict[MerchantCategory, tuple[str, ...]] = {
    MerchantCategory.GROCERY: ("Fresh Market", "Superstore", "Grocers", "Foods"),
    MerchantCategory.FUEL: ("Fuel Stop", "Service Station", "Petroleum"),
    MerchantCategory.RESTAURANT: ("Grill", "Kitchen", "Bistro", "Eatery"),
    MerchantCategory.CLOTHING: ("Outfitters", "Fashion", "Apparel"),
    MerchantCategory.BETTING: ("Bets", "Sportsbook"),
    MerchantCategory.ONLINE_RETAIL: ("Online", "Shop", "Store"),
    MerchantCategory.DIGITAL_GOODS: ("Games", "Play", "Media"),
    MerchantCategory.AIRTIME: ("Airtime", "Mobile Top-Up"),
    MerchantCategory.SOFTWARE: ("Software", "Apps", "Cloud"),
    MerchantCategory.TRAVEL_AGENCY: ("Travel", "Trips", "Flights"),
    MerchantCategory.CRYPTO: ("Coin Exchange", "Digital Assets"),
    MerchantCategory.ADVERTISING: ("Ads", "Ad Network", "Promotions"),
}


@dataclass(frozen=True, slots=True)
class _MerchantDraft:
    """A merchant before it gets an ID and location."""

    category: MerchantCategory
    name: str
    country_code: str
    city: City | None  # None for online merchants


def generate_merchants(rng: random.Random) -> list[Merchant]:
    """Generate all merchants. The list does not depend on the number of customers."""
    drafts = [
        *_physical_drafts(rng),
        *_toll_drafts(),
        *_online_drafts(rng, DOMESTIC_ONLINE_COUNTS, {HOME_COUNTRY: 1}),
        *_online_drafts(rng, FOREIGN_ONLINE_COUNTS, FOREIGN_COUNTRY_WEIGHTS),
    ]
    return [_make_merchant(rng, number, draft) for number, draft in enumerate(drafts, start=1)]


def _physical_drafts(rng: random.Random) -> list[_MerchantDraft]:
    drafts = []
    for city in CITIES:
        for category, per_weight in PHYSICAL_MERCHANTS_PER_WEIGHT.items():
            count = round(city.weight * per_weight)
            if category in ESSENTIAL_CATEGORIES:
                count = max(count, 1)
            for index in range(1, count + 1):
                name = _physical_name(rng, category, city, index)
                drafts.append(_MerchantDraft(category, name, HOME_COUNTRY, city))
    return drafts


def _toll_drafts() -> list[_MerchantDraft]:
    cities_by_name = {city.name: city for city in CITIES}
    return [
        _MerchantDraft(
            MerchantCategory.TOLL, f"{name} Toll Plaza", HOME_COUNTRY, cities_by_name[name]
        )
        for name in TOLL_PLAZA_CITIES
    ]


def _online_drafts(
    rng: random.Random,
    counts: Mapping[MerchantCategory, int],
    country_weights: Mapping[str, float],
) -> list[_MerchantDraft]:
    return [
        _MerchantDraft(category, _online_name(rng, category), pick(rng, country_weights), None)
        for category, count in counts.items()
        for _ in range(count)
    ]


def _make_merchant(rng: random.Random, number: int, draft: _MerchantDraft) -> Merchant:
    lat, lon = (None, None)
    if draft.city is not None:
        lat, lon = jitter_location(rng, draft.city, MERCHANT_JITTER_DEGREES)
    return Merchant(
        merchant_id=make_id("MER", number),
        merchant_name=draft.name,
        mcc=rng.choice(MCCS_BY_CATEGORY[draft.category]),
        category=draft.category,
        country_code=draft.country_code,
        city=draft.city.name if draft.city else None,
        lat=lat,
        lon=lon,
        is_online=draft.city is None,
    )


def _physical_name(rng: random.Random, category: MerchantCategory, city: City, index: int) -> str:
    if category is MerchantCategory.ATM:
        return f"{city.name} ATM {index:03d}"
    return f"{rng.choice(BUSINESS_NAME_WORDS)} {rng.choice(NAME_ENDINGS[category])}"


def _online_name(rng: random.Random, category: MerchantCategory) -> str:
    return f"{rng.choice(ONLINE_NAME_WORDS)} {rng.choice(NAME_ENDINGS[category])}"
