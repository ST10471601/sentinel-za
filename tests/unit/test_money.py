from decimal import Decimal

import pytest

from sentinel.core.errors import DataValidationError
from sentinel.core.money import format_zar, to_cents, to_rands


@pytest.mark.parametrize(
    ("rands", "expected_cents"),
    [
        ("1234.56", 123456),
        (Decimal("10"), 1000),
        (50, 5000),
        ("0.005", 1),  # half a cent rounds up
        ("0.004", 0),
        ("-12.34", -1234),
    ],
)
def test_to_cents_converts_supported_types(rands: Decimal | int | str, expected_cents: int) -> None:
    assert to_cents(rands) == expected_cents


@pytest.mark.parametrize("bad_type", [0.1, True])
def test_to_cents_rejects_floats_and_bools(bad_type: object) -> None:
    with pytest.raises(TypeError):
        to_cents(bad_type)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["abc", "", "NaN", "Infinity"])
def test_to_cents_rejects_non_numeric_or_non_finite_values(bad_value: str) -> None:
    with pytest.raises(DataValidationError):
        to_cents(bad_value)


def test_to_rands_returns_two_decimal_places() -> None:
    assert to_rands(123456) == Decimal("1234.56")
    assert str(to_rands(500)) == "5.00"


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (123456, "R1,234.56"),
        (0, "R0.00"),
        (-500, "-R5.00"),
        (100_000_000, "R1,000,000.00"),
    ],
)
def test_format_zar(cents: int, expected: str) -> None:
    assert format_zar(cents) == expected
