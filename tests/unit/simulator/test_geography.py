from sentinel.domain.customers import Province
from sentinel.simulator.geography import CITIES

# Rough bounding box of South Africa.
SA_LATITUDES = (-35.0, -22.0)
SA_LONGITUDES = (16.0, 33.0)


def test_every_province_has_at_least_one_city() -> None:
    assert {city.province for city in CITIES} == set(Province)


def test_city_names_are_unique() -> None:
    names = [city.name for city in CITIES]
    assert len(names) == len(set(names))


def test_cities_lie_inside_south_africa() -> None:
    for city in CITIES:
        assert SA_LATITUDES[0] <= city.lat <= SA_LATITUDES[1], city.name
        assert SA_LONGITUDES[0] <= city.lon <= SA_LONGITUDES[1], city.name


def test_weights_are_positive_and_gauteng_is_largest() -> None:
    assert all(city.weight > 0 for city in CITIES)

    weight_by_province = dict.fromkeys(Province, 0.0)
    for city in CITIES:
        weight_by_province[city.province] += city.weight
    assert max(weight_by_province, key=weight_by_province.__getitem__) is Province.GAUTENG
