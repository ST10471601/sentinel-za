import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.core.errors import SimulationError
from sentinel.simulator.randomness import (
    chance,
    draw_unique,
    make_rng,
    pick,
    random_datetime_between,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def draws(rng: random.Random) -> list[float]:
    return [rng.random() for _ in range(5)]


def test_same_seed_and_stream_repeat_exactly() -> None:
    assert draws(make_rng(42, "customers")) == draws(make_rng(42, "customers"))


def test_streams_and_seeds_are_independent() -> None:
    assert draws(make_rng(42, "customers")) != draws(make_rng(42, "merchants"))
    assert draws(make_rng(42, "customers")) != draws(make_rng(43, "customers"))


def test_pick_follows_weights() -> None:
    rng = random.Random(1)
    counts = Counter(pick(rng, {"common": 9, "rare": 1, "never": 0}) for _ in range(2_000))
    assert counts["never"] == 0
    assert 1_700 <= counts["common"] <= 1_900


def test_chance_at_the_extremes() -> None:
    rng = random.Random(1)
    assert not any(chance(rng, 0.0) for _ in range(100))
    assert all(chance(rng, 1.0) for _ in range(100))


def test_draw_unique_records_new_values() -> None:
    used = {"a"}
    values = iter(["a", "a", "b"])
    assert draw_unique(lambda: next(values), used) == "b"
    assert used == {"a", "b"}


def test_draw_unique_gives_up_when_values_run_out() -> None:
    with pytest.raises(SimulationError, match="no unique value"):
        draw_unique(lambda: "same", {"same"})


def test_random_datetime_between_stays_in_range_with_whole_seconds() -> None:
    rng = random.Random(1)
    end = START + timedelta(minutes=5)
    for _ in range(500):
        value = random_datetime_between(rng, START, end)
        assert START <= value < end
        assert value.microsecond == 0


@pytest.mark.parametrize("end", [START, START - timedelta(days=1)])
def test_random_datetime_between_needs_a_real_range(end: datetime) -> None:
    with pytest.raises(ValueError, match="at least one second"):
        random_datetime_between(random.Random(1), START, end)
