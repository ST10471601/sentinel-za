"""South African cities used for customer homes and merchant locations."""

import random
from dataclasses import dataclass

from sentinel.domain.customers import Province

COORDINATE_DECIMALS = 4  # about 11 m


@dataclass(frozen=True, slots=True)
class City:
    """A city and its approximate centre.

    ``weight`` is the city's relative share of customers. Province totals follow
    Stats SA mid-year population estimates.
    """

    name: str
    province: Province
    lat: float
    lon: float
    weight: float


CITIES: tuple[City, ...] = (
    # Gauteng (about 27%)
    City("Johannesburg", Province.GAUTENG, -26.2041, 28.0473, 8.0),
    City("Soweto", Province.GAUTENG, -26.2485, 27.8540, 4.0),
    City("Pretoria", Province.GAUTENG, -25.7479, 28.2293, 6.0),
    City("Centurion", Province.GAUTENG, -25.8603, 28.1894, 2.0),
    City("Tembisa", Province.GAUTENG, -25.9964, 28.2268, 2.0),
    City("Benoni", Province.GAUTENG, -26.1885, 28.3208, 2.0),
    City("Vereeniging", Province.GAUTENG, -26.6731, 27.9261, 2.0),
    City("Midrand", Province.GAUTENG, -25.9992, 28.1263, 1.0),
    # KwaZulu-Natal (about 19%)
    City("Durban", Province.KWAZULU_NATAL, -29.8587, 31.0218, 9.0),
    City("Umlazi", Province.KWAZULU_NATAL, -29.9667, 30.8833, 3.0),
    City("Pietermaritzburg", Province.KWAZULU_NATAL, -29.6006, 30.3794, 4.0),
    City("Richards Bay", Province.KWAZULU_NATAL, -28.7807, 32.0383, 1.5),
    City("Newcastle", Province.KWAZULU_NATAL, -27.7579, 29.9318, 1.5),
    # Western Cape (about 12%)
    City("Cape Town", Province.WESTERN_CAPE, -33.9249, 18.4241, 9.0),
    City("Stellenbosch", Province.WESTERN_CAPE, -33.9321, 18.8602, 1.0),
    City("Paarl", Province.WESTERN_CAPE, -33.7342, 18.9621, 1.0),
    City("George", Province.WESTERN_CAPE, -33.9630, 22.4617, 1.0),
    # Eastern Cape (about 11%)
    City("Gqeberha", Province.EASTERN_CAPE, -33.9608, 25.6022, 4.0),
    City("East London", Province.EASTERN_CAPE, -33.0153, 27.9116, 3.0),
    City("Mthatha", Province.EASTERN_CAPE, -31.5889, 28.7844, 4.0),
    # Limpopo (about 10%)
    City("Polokwane", Province.LIMPOPO, -23.9045, 29.4689, 5.0),
    City("Thohoyandou", Province.LIMPOPO, -22.9456, 30.4850, 3.0),
    City("Tzaneen", Province.LIMPOPO, -23.8332, 30.1635, 2.0),
    # Mpumalanga (about 8%)
    City("Mbombela", Province.MPUMALANGA, -25.4658, 30.9853, 3.5),
    City("eMalahleni", Province.MPUMALANGA, -25.8728, 29.2553, 3.0),
    City("Secunda", Province.MPUMALANGA, -26.5504, 29.1781, 1.5),
    # North West (about 7%)
    City("Rustenburg", Province.NORTH_WEST, -25.6676, 27.2421, 3.0),
    City("Mahikeng", Province.NORTH_WEST, -25.8560, 25.6403, 1.5),
    City("Klerksdorp", Province.NORTH_WEST, -26.8521, 26.6667, 1.5),
    City("Potchefstroom", Province.NORTH_WEST, -26.7145, 27.0970, 1.0),
    # Free State (about 5%)
    City("Bloemfontein", Province.FREE_STATE, -29.0852, 26.1596, 3.0),
    City("Welkom", Province.FREE_STATE, -27.9774, 26.7351, 1.5),
    # Northern Cape (about 2%)
    City("Kimberley", Province.NORTHERN_CAPE, -28.7282, 24.7499, 1.5),
    City("Upington", Province.NORTHERN_CAPE, -28.4478, 21.2561, 0.5),
)


def jitter_location(rng: random.Random, city: City, max_degrees: float) -> tuple[float, float]:
    """Return a point up to ``max_degrees`` from the city centre in each direction."""
    lat = city.lat + rng.uniform(-max_degrees, max_degrees)
    lon = city.lon + rng.uniform(-max_degrees, max_degrees)
    return round(lat, COORDINATE_DECIMALS), round(lon, COORDINATE_DECIMALS)
