import pytest

from sentinel.core.luhn import is_luhn_valid, luhn_check_digit


@pytest.mark.parametrize(
    ("payload", "expected_digit"),
    [
        ("7992739871", "3"),  # standard worked example
        ("800101500908", "7"),  # SA ID number
        ("0", "0"),
    ],
)
def test_luhn_check_digit(payload: str, expected_digit: str) -> None:
    assert luhn_check_digit(payload) == expected_digit


@pytest.mark.parametrize("bad_payload", ["", "12a4", "12 34", "²"])
def test_luhn_check_digit_rejects_non_digits(bad_payload: str) -> None:
    with pytest.raises(ValueError, match="only digits"):
        luhn_check_digit(bad_payload)


@pytest.mark.parametrize("number", ["79927398713", "8001015009087"])
def test_is_luhn_valid_accepts_correct_numbers(number: str) -> None:
    assert is_luhn_valid(number)


@pytest.mark.parametrize("number", ["79927398710", "8001015009088", "7", "", "8001O15009087"])
def test_is_luhn_valid_rejects_wrong_or_malformed_numbers(number: str) -> None:
    assert not is_luhn_valid(number)
