import random
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from sentinel.core.datetimes import to_sast
from sentinel.simulator.fraud.episodes import Victim
from sentinel.simulator.fraud.skimming_sites import (
    CLONED_PER_SITE,
    SITE_DAYS,
    SkimmingSite,
    open_site,
)

DAY = date(2026, 1, 14)


def regulars_per_atm(victims: list[Victim]) -> Counter[str]:
    return Counter(atm.merchant_id for v in victims for atm in v.favourite_atms)


def test_a_skimmer_is_fitted_for_one_to_three_weeks(victims: list[Victim]) -> None:
    for seed in range(50):
        site = open_site(random.Random(seed), victims, DAY)

        assert site is not None
        assert site.closes == DAY
        assert SITE_DAYS[0] <= (site.closes - site.opens).days + 1 <= SITE_DAYS[1]
        assert CLONED_PER_SITE[0] <= site.cards_cloned <= CLONED_PER_SITE[1]


def test_busy_atms_are_chosen_more_often(victims: list[Victim]) -> None:
    regulars = regulars_per_atm(victims)
    sites = [open_site(random.Random(seed), victims, DAY) for seed in range(300)]
    chosen = [regulars[site.atm.merchant_id] for site in sites if site is not None]

    # Weighting by regulars favours ATMs busier than the average one.
    assert sum(chosen) / len(chosen) > sum(regulars.values()) / len(regulars)


def test_regulars_are_the_customers_who_use_the_atm(victims: list[Victim]) -> None:
    site = open_site(random.Random(1), victims, DAY)
    assert site is not None

    regulars = site.regulars(victims)

    assert len(regulars) == regulars_per_atm(victims)[site.atm.merchant_id]
    assert all(site.atm in victim.favourite_atms for victim in regulars)


def test_cards_are_skimmed_while_the_skimmer_is_fitted(victims: list[Victim]) -> None:
    site = SkimmingSite(victims[0].favourite_atms[0], DAY, DAY + timedelta(days=6), 3)
    rng = random.Random(2)

    days = {to_sast(site.skim_time(rng)).date() for _ in range(200)}

    assert days == {DAY + timedelta(days=n) for n in range(7)}


def test_no_site_without_atms(victims: list[Victim]) -> None:
    no_atms = [replace(victim, favourite_atms=()) for victim in victims[:20]]
    assert open_site(random.Random(1), no_atms, DAY) is None
