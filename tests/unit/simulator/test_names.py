import pytest

from sentinel.simulator.names import (
    BUSINESS_NAME_WORDS,
    BUSINESS_TRADES,
    FEMALE_FIRST_NAMES,
    MALE_FIRST_NAMES,
    SURNAMES,
)

# A full run has about 500 businesses; each needs a unique name.
MIN_BUSINESS_NAME_COMBINATIONS = 500


@pytest.mark.parametrize(
    "names",
    [FEMALE_FIRST_NAMES, MALE_FIRST_NAMES, SURNAMES, BUSINESS_NAME_WORDS, BUSINESS_TRADES],
)
def test_name_lists_are_non_empty_unique_and_trimmed(names: tuple[str, ...]) -> None:
    assert names
    assert len(names) == len(set(names))
    assert all(name == name.strip() and name for name in names)


def test_first_name_lists_do_not_overlap() -> None:
    assert not set(FEMALE_FIRST_NAMES) & set(MALE_FIRST_NAMES)


def test_enough_business_name_combinations() -> None:
    combinations = len(BUSINESS_NAME_WORDS) * len(BUSINESS_TRADES)
    assert combinations >= MIN_BUSINESS_NAME_COMBINATIONS
