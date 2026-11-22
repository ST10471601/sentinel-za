import random

from sentinel.simulator.fraud.payouts import RAND_CENTS, split, whole_rands


def test_split_gives_whole_rand_shares_that_add_up() -> None:
    rng = random.Random(1)
    for total in (100, 12_345_600, 5_000_000):
        for parts in (1, 2, 3):
            shares = split(rng, total, parts)
            assert 1 <= len(shares) <= parts
            assert sum(shares) == total
            assert all(share > 0 and share % RAND_CENTS == 0 for share in shares)


def test_split_never_makes_more_shares_than_rands() -> None:
    rng = random.Random(2)
    assert split(rng, 200, 3) == [100, 100]
    assert split(rng, 99, 3) == []
    assert split(rng, -5_000, 2) == []


def test_whole_rands_drops_the_cents() -> None:
    assert whole_rands(123_456) == 123_400
    assert whole_rands(100) == 100
    assert whole_rands(99) == 0
