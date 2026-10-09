"""Merchants where cards are used. ATMs are merchants too, with MCC 6011."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.domain.base import CountryCode, DomainModel, Latitude, Longitude

MerchantId = Annotated[str, Field(pattern=r"^MER-\d{7}$")]
MerchantCategoryCode = Annotated[str, Field(pattern=r"^\d{4}$")]  # ISO 18245


class MerchantCategory(StrEnum):
    """Readable merchant category."""

    GROCERY = "grocery"
    FUEL = "fuel"
    TOLL = "toll"
    ATM = "atm"
    RESTAURANT = "restaurant"
    CLOTHING = "clothing"
    ONLINE_RETAIL = "online_retail"
    DIGITAL_GOODS = "digital_goods"
    SOFTWARE = "software"
    ADVERTISING = "advertising"
    TRAVEL_AGENCY = "travel_agency"
    BETTING = "betting"
    CRYPTO = "crypto"
    AIRTIME = "airtime"


# Merchant category codes (MCCs) allowed for each category.
MCCS_BY_CATEGORY: dict[MerchantCategory, tuple[str, ...]] = {
    MerchantCategory.GROCERY: ("5411",),
    MerchantCategory.FUEL: ("5541",),
    MerchantCategory.TOLL: ("4784",),
    MerchantCategory.ATM: ("6011",),
    MerchantCategory.RESTAURANT: ("5812",),
    MerchantCategory.CLOTHING: ("5651",),
    MerchantCategory.ONLINE_RETAIL: ("5999",),
    MerchantCategory.DIGITAL_GOODS: ("5815", "5816", "5817", "5818"),
    MerchantCategory.SOFTWARE: ("5734",),
    MerchantCategory.ADVERTISING: ("7311",),
    MerchantCategory.TRAVEL_AGENCY: ("4722",),
    MerchantCategory.BETTING: ("7995",),
    MerchantCategory.CRYPTO: ("6051",),  # quasi-cash, includes crypto exchanges
    MerchantCategory.AIRTIME: ("4814",),
}


class Merchant(DomainModel):
    """A card merchant or ATM.

    Online merchants have no physical location; physical merchants always have one.
    """

    merchant_id: MerchantId
    merchant_name: str = Field(min_length=1)
    mcc: MerchantCategoryCode
    category: MerchantCategory
    country_code: CountryCode
    city: str | None
    lat: Latitude | None
    lon: Longitude | None
    is_online: bool

    @model_validator(mode="after")
    def check_mcc_matches_category(self) -> Self:
        """The MCC must be one of the codes for the category."""
        if self.mcc not in MCCS_BY_CATEGORY[self.category]:
            raise ValueError(f"mcc {self.mcc} does not belong to category {self.category}")
        return self

    @model_validator(mode="after")
    def check_location(self) -> Self:
        """Physical merchants need city and coordinates; online merchants have none."""
        location = (self.city, self.lat, self.lon)
        if self.is_online and any(part is not None for part in location):
            raise ValueError("online merchants have no city or coordinates")
        if not self.is_online and any(part is None for part in location):
            raise ValueError("physical merchants need city, lat and lon")
        return self
