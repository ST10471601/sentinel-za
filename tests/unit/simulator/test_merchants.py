from collections import Counter

from sentinel.domain.merchants import MerchantCategory
from sentinel.simulator.geography import CITIES
from sentinel.simulator.merchants import (
    DOMESTIC_ONLINE_COUNTS,
    ESSENTIAL_CATEGORIES,
    FOREIGN_ONLINE_COUNTS,
    HOME_COUNTRY,
    TOLL_PLAZA_CITIES,
)
from sentinel.simulator.reference_data import ReferenceData


def test_ids_are_sequential(reference_data: ReferenceData) -> None:
    ids = [merchant.merchant_id for merchant in reference_data.merchants]
    assert ids == [f"MER-{number:07d}" for number in range(1, len(ids) + 1)]


def test_every_city_has_essential_merchants(reference_data: ReferenceData) -> None:
    present = {(m.city, m.category) for m in reference_data.merchants if not m.is_online}
    for city in CITIES:
        for category in ESSENTIAL_CATEGORIES:
            assert (city.name, category) in present, (city.name, category)


def test_toll_plazas_are_in_their_cities(reference_data: ReferenceData) -> None:
    toll_cities = {m.city for m in reference_data.merchants if m.category is MerchantCategory.TOLL}
    assert toll_cities == set(TOLL_PLAZA_CITIES)


def test_online_merchant_counts_match_settings(reference_data: ReferenceData) -> None:
    domestic = Counter(
        m.category
        for m in reference_data.merchants
        if m.is_online and m.country_code == HOME_COUNTRY
    )
    foreign = Counter(
        m.category for m in reference_data.merchants if m.country_code != HOME_COUNTRY
    )
    assert domestic == Counter(DOMESTIC_ONLINE_COUNTS)
    assert foreign == Counter(FOREIGN_ONLINE_COUNTS)


def test_foreign_merchants_are_all_online(reference_data: ReferenceData) -> None:
    foreign = [m for m in reference_data.merchants if m.country_code != HOME_COUNTRY]
    assert foreign
    assert all(m.is_online for m in foreign)


def test_physical_merchants_are_near_their_city(reference_data: ReferenceData) -> None:
    centre = {city.name: (city.lat, city.lon) for city in CITIES}
    for merchant in reference_data.merchants:
        if merchant.is_online:
            continue
        assert merchant.city is not None
        assert merchant.lat is not None
        assert merchant.lon is not None
        lat, lon = centre[merchant.city]
        assert abs(merchant.lat - lat) <= 0.1
        assert abs(merchant.lon - lon) <= 0.1
