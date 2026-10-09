"""Seeded random streams and sampling helpers used by every generator."""

import random
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from sentinel.core.errors import SimulationError

MAX_UNIQUE_ATTEMPTS = 1_000


def make_rng(seed: int, stream: str) -> random.Random:
    """Return the random generator for one named part of the simulation.

    Each generator (customers, accounts, ...) gets its own stream, so changing how one
    table is generated never changes the output of another.
    """
    # String seeds are hashed with SHA-512, so they are stable across runs and Python versions.
    return random.Random(f"{seed}:{stream}")


def pick[T](rng: random.Random, weights: Mapping[T, float]) -> T:
    """Pick one key, with probability proportional to its weight."""
    return rng.choices(tuple(weights), weights=tuple(weights.values()))[0]


def chance(rng: random.Random, probability: float) -> bool:
    """Return True with the given probability."""
    return rng.random() < probability


def draw_unique[T](make_value: Callable[[], T], used: set[T]) -> T:
    """Call ``make_value`` until it returns a value not in ``used``, then record it."""
    for _ in range(MAX_UNIQUE_ATTEMPTS):
        value = make_value()
        if value not in used:
            used.add(value)
            return value
    raise SimulationError(f"no unique value found after {MAX_UNIQUE_ATTEMPTS} attempts")


def random_datetime_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """Return a whole-second datetime from ``start`` (inclusive) to ``end`` (exclusive)."""
    span_seconds = int((end - start).total_seconds())
    if span_seconds < 1:
        raise ValueError(f"end must be at least one second after start: {start} to {end}")
    return start + timedelta(seconds=rng.randrange(span_seconds))
