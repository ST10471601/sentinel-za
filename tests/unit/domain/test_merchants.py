import pytest
from pydantic import ValidationError

from sentinel.domain.merchants import MCCS_BY_CATEGORY, Merchant, MerchantCategory


def physical_merchant_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "merchant_id": "MER-0000001",
        "merchant_name": "Protea Fresh Market",
        "mcc": "5411",
        "category": MerchantCategory.GROCERY,
        "country_code": "ZA",
        "city": "Durban",
        "lat": -29.8587,
        "lon": 31.0218,
        "is_online": False,
    }
    return fields | overrides


def online_merchant_fields(**overrides: object) -> dict[str, object]:
    fields = physical_merchant_fields(
        merchant_name="Pixelvault Games",
        mcc="5816",
        category=MerchantCategory.DIGITAL_GOODS,
        country_code="NL",
        city=None,
        lat=None,
        lon=None,
        is_online=True,
    )
    return fields | overrides


def test_every_category_has_four_digit_mccs() -> None:
    assert set(MCCS_BY_CATEGORY) == set(MerchantCategory)
    for mccs in MCCS_BY_CATEGORY.values():
        assert mccs
        assert all(len(mcc) == 4 and mcc.isdigit() for mcc in mccs)


def test_valid_physical_and_online_merchants_are_accepted() -> None:
    assert not Merchant.model_validate(physical_merchant_fields()).is_online
    assert Merchant.model_validate(online_merchant_fields()).is_online


def test_mcc_must_belong_to_category() -> None:
    with pytest.raises(ValidationError, match="does not belong to category"):
        Merchant.model_validate(physical_merchant_fields(mcc="6011"))


@pytest.mark.parametrize("missing", ["city", "lat", "lon"])
def test_physical_merchant_needs_a_location(missing: str) -> None:
    with pytest.raises(ValidationError, match="physical merchants need"):
        Merchant.model_validate(physical_merchant_fields(**{missing: None}))


def test_online_merchant_has_no_location() -> None:
    with pytest.raises(ValidationError, match="online merchants have no"):
        Merchant.model_validate(online_merchant_fields(city="Amsterdam"))


@pytest.mark.parametrize("country_code", ["za", "ZAF", "Z"])
def test_country_code_must_be_iso_alpha_2(country_code: str) -> None:
    with pytest.raises(ValidationError):
        Merchant.model_validate(physical_merchant_fields(country_code=country_code))
