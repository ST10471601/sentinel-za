import random
import re
from datetime import date

import pytest

from sentinel.core.luhn import is_luhn_valid
from sentinel.simulator.identifiers import (
    IdSequence,
    make_account_number,
    make_card_token,
    make_company_reg_number,
    make_device_fingerprint,
    make_id,
    make_pan_last4,
    make_phone_number,
    make_sa_id_number,
)

SAMPLE_SIZE = 200


@pytest.fixture
def rng() -> random.Random:
    return random.Random(7)


def test_make_id_pads_to_seven_digits_by_default() -> None:
    assert make_id("CUS", 42) == "CUS-0000042"
    assert make_id("MER", 9_999_999) == "MER-9999999"


def test_make_id_can_use_more_digits() -> None:
    assert make_id("TXN", 42, digits=9) == "TXN-000000042"


def test_id_sequence_counts_up_from_one() -> None:
    session_ids = IdSequence("SES")
    assert [session_ids.next_id() for _ in range(3)] == [
        "SES-0000001",
        "SES-0000002",
        "SES-0000003",
    ]
    assert IdSequence("TXN", digits=9).next_id() == "TXN-000000001"


@pytest.mark.parametrize("number", [0, -1, 10_000_000])
def test_make_id_rejects_numbers_out_of_range(number: int) -> None:
    with pytest.raises(ValueError, match="between 1 and"):
        make_id("CUS", number)


@pytest.mark.parametrize("is_female", [True, False])
def test_sa_id_number_encodes_birth_date_gender_and_check_digit(
    rng: random.Random, is_female: bool
) -> None:
    for _ in range(SAMPLE_SIZE):
        id_number = make_sa_id_number(rng, date(1987, 11, 3), is_female=is_female)

        assert len(id_number) == 13
        assert id_number.startswith("871103")
        assert (int(id_number[6:10]) < 5000) is is_female
        assert id_number[10:12] == "08"  # citizen, then the fixed 8
        assert is_luhn_valid(id_number)


def test_phone_numbers_are_sa_mobile_numbers(rng: random.Random) -> None:
    for _ in range(SAMPLE_SIZE):
        assert re.fullmatch(r"\+27[6-8]\d{8}", make_phone_number(rng))


def test_account_numbers_have_ten_digits_and_no_leading_zero(rng: random.Random) -> None:
    for _ in range(SAMPLE_SIZE):
        assert re.fullmatch(r"[1-9]\d{9}", make_account_number(rng))


def test_company_reg_number_uses_cipc_format(rng: random.Random) -> None:
    assert re.fullmatch(r"2015/\d{6}/07", make_company_reg_number(rng, 2015))


def test_card_and_device_references_have_fixed_formats(rng: random.Random) -> None:
    for _ in range(SAMPLE_SIZE):
        assert re.fullmatch(r"tok_[0-9a-f]{24}", make_card_token(rng))
        assert re.fullmatch(r"\d{4}", make_pan_last4(rng))
        assert re.fullmatch(r"[0-9a-f]{64}", make_device_fingerprint(rng))


def test_same_seed_gives_same_values() -> None:
    first, second = random.Random(1), random.Random(1)
    assert [make_phone_number(first) for _ in range(5)] == [
        make_phone_number(second) for _ in range(5)
    ]
