"""Counterfeit cards: the card and PIN are skimmed, then a clone is used in another city.

SABRIC 2025 gives the losses (debit R13.6m, credit R4.4m) but no description, so the shape is
the standard pattern and a simulation assumption. Debit cards are caught several at a time by a
skimming site at an ATM; credit cards one at a time at a shop's card machine. Days or weeks
later a crew uses magstripe clones and the PINs far from home, card after card. The victims
still have their real cards and keep using them, so each card is in two places at once.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import Card, CardType
from sentinel.domain.customers import Customer
from sentinel.domain.labels import FraudType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.simulator.card_activity import (
    MerchantIndex,
    atm_withdrawal_draft,
    store_purchase_draft,
    swiped,
)
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.cash_out import DRIVE_SECONDS, Round, take_cash
from sentinel.simulator.fraud.episodes import (
    Balances,
    FraudAction,
    FraudActions,
    ParamValue,
    Victim,
)
from sentinel.simulator.fraud.skimming_sites import SkimmingSite, open_site
from sentinel.simulator.geography import CITIES, City
from sentinel.simulator.randomness import chance, pick, random_datetime_between
from sentinel.simulator.spending import atm_amount, card_amount, pick_event_time
from sentinel.simulator.unusual_events import away_customer

DEBIT_CARD_SHARE = 0.76  # debit losses R13.6m vs credit R4.4m

# Simulation assumptions from here on.
NEARBY_MERCHANTS = 6
CREW_ATMS = 3  # a crew works a few ATMs, so several clones pass through each within minutes
# Customers here never draw cash on a credit card, so an ATM skim of one would be a fraud-only
# signal. Credit cards are skimmed at a shop's card machine, where the card leaves sight.
CREDIT_SKIM_CATEGORIES = (MerchantCategory.FUEL, MerchantCategory.RESTAURANT)
FIRST_USE_DAYS = (3, 30)  # after the skim, or after a site closes, once the clones are made
DAYS_USED = (1, 3)  # days in a row
ROUND_HOURS = (time(8), time(20))  # the crew sets out in shop hours; a round ends at midnight
CARD_GAP_SECONDS = (60, 300)  # between starting one card and the next
PURCHASES = (1, 4)
# Goods that are easy to use or resell.
PURCHASE_CENTS: dict[MerchantCategory, tuple[int, int]] = {
    MerchantCategory.FUEL: (30_000, 150_000),  # up to a full tank
    MerchantCategory.CLOTHING: (50_000, 500_000),
    MerchantCategory.GROCERY: (20_000, 250_000),
}


@dataclass(frozen=True, slots=True)
class CashOut:
    """Where and when a crew uses clones: one city, on a day or a few in a row."""

    city: City
    day_starts: tuple[datetime, ...]  # when the crew sets out each day
    atms: tuple[Merchant, ...]
    shops: tuple[Merchant, ...]

    def rounds(self, delay: timedelta) -> tuple[Round, ...]:
        """One round a day for a card the crew starts ``delay`` after setting out."""
        return tuple(Round(start + delay, _midnight_after(start)) for start in self.day_starts)


@dataclass(slots=True)
class Crew:
    """The crew cloning one skimming site's cards, and how many cards it has taken on."""

    site: SkimmingSite
    cash_out: CashOut
    cards_taken: int = 0
    next_delay: timedelta = field(default_factory=timedelta)

    def regulars(self, candidates: Sequence[Victim]) -> list[Victim]:
        """Regulars at the site whose cards can still be cloned; none once the batch is full."""
        if self.cards_taken >= self.site.cards_cloned:
            return []
        return self.site.regulars(candidates)

    def take_card(self, rng: random.Random) -> timedelta:
        """Add a card to the batch, and say how long after setting out the crew starts it."""
        delay = self.next_delay
        self.cards_taken += 1
        self.next_delay += _seconds(rng, CARD_GAP_SECONDS)
        return delay


@dataclass(frozen=True, slots=True)
class CounterfeitEpisode:
    """A cloned card used for a day or a few far from home, while the victim keeps the real one."""

    scenario_id: str
    victim_customer_id: str
    params: Mapping[str, ParamValue]
    card: Card
    skim: TransactionDraft  # the victim's own transaction where the card was skimmed, not fraud
    rounds: tuple[Round, ...]
    atms: tuple[Merchant, ...]
    shops: tuple[Merchant, ...]
    seed: int  # each round draws from its own stream, so the order days are played can't matter

    fraud_type = FraudType.CARD_PRESENT_COUNTERFEIT

    @property
    def card_id(self) -> str:
        """The skimmed card, blocked when the fraud is reported."""
        return self.card.card_id

    @property
    def card_loss(self) -> None:
        """The victim still has the real card."""
        return None

    def days(self) -> frozenset[date]:
        """SA calendar days of the skim and of each round."""
        times = [self.skim.event_time, *(spree.start for spree in self.rounds)]
        return frozenset(_sa_day(moment) for moment in times)

    def planned_end(self) -> datetime:
        """Midnight after the last day the clone is used."""
        return self.rounds[-1].end

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The victim's skimmed transaction, or the clone's round, on ``day``."""
        actions = FraudActions()
        if _sa_day(self.skim.event_time) == day:
            actions.genuine.append(self.skim)
        for spree in self.rounds:
            if _sa_day(spree.start) == day:
                available = balances.available_cents(self.card.account_id)
                actions.attempts += self._play(spree, available)
        return actions

    def _play(self, spree: Round, available_cents: int) -> list[FraudAction]:
        """Cash first, then shopping, all with the swiped clone and the skimmed PIN."""
        rng = random.Random(f"{self.seed}:{spree.start.isoformat()}")
        attempts, moment = take_cash(rng, self.card, self.atms, spree, available_cents)
        for _ in range(rng.randint(*PURCHASES)):
            moment += _seconds(rng, DRIVE_SECONDS)
            if moment >= spree.end:
                break
            shop = rng.choice(self.shops)
            amount = rng.randint(*PURCHASE_CENTS[shop.category])
            attempts.append(store_purchase_draft(self.card, shop, amount, moment, False))
        return [swiped(a) if isinstance(a, TransactionDraft) else a for a in attempts]


class Counterfeit:
    """Plans counterfeit card episodes: a card skimmed at a site or a shop, then its clone used."""

    fraud_type = FraudType.CARD_PRESENT_COUNTERFEIT

    def __init__(self, merchants: Sequence[Merchant]) -> None:
        self._index = MerchantIndex(merchants)
        self._crew: Crew | None = None  # working the latest skimming site
        self._chosen: dict[str, tuple[Crew, timedelta]] = {}  # site victims, by customer ID

    def choose_victim(
        self, rng: random.Random, candidates: Sequence[Victim], start: datetime
    ) -> Victim | None:
        """Mostly a regular at a skimming site; otherwise a credit card holder, for a shop skim."""
        if not chance(rng, DEBIT_CARD_SHARE):
            holders = [victim for victim in candidates if _credit_card(victim) is not None]
            if holders:
                return rng.choice(holders)
        return self._take_regular(rng, candidates, _sa_day(start))

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> CounterfeitEpisode:
        """Plan the clone's use from ``start``'s day, and the skim before it."""
        chosen = self._chosen.pop(victim.customer.customer_id, None)
        if chosen is None:
            return self._plan_shop_skim(rng, victim, start, scenario_id)
        crew, delay = chosen
        return self._plan_site_skim(rng, victim, crew, delay, scenario_id)

    def _take_regular(
        self, rng: random.Random, candidates: Sequence[Victim], first_day: date
    ) -> Victim | None:
        """The next regular at the latest site, or the first at a new one once that is used up."""
        crew = self._crew
        regulars = crew.regulars(candidates) if crew is not None else []
        if crew is None or not regulars:
            crew = self._start_crew(rng, candidates, first_day)
            if crew is None:
                return None
            regulars = crew.regulars(candidates)
        victim = rng.choice(regulars)
        self._chosen[victim.customer.customer_id] = (crew, crew.take_card(rng))
        return victim

    def _start_crew(
        self, rng: random.Random, candidates: Sequence[Victim], first_day: date
    ) -> Crew | None:
        """A crew that starts cashing out on ``first_day``, and the site it skimmed before."""
        closes = first_day - timedelta(days=rng.randint(*FIRST_USE_DAYS))
        site = open_site(rng, candidates, closes)
        if site is None:
            return None
        local = site.regulars(candidates)[0].customer
        self._crew = Crew(site, self._plan_cash_out(rng, local, first_day))
        return self._crew

    def _plan_site_skim(
        self, rng: random.Random, victim: Victim, crew: Crew, delay: timedelta, scenario_id: str
    ) -> CounterfeitEpisode:
        """A regular's debit card caught at the site, then cloned with the rest of the batch."""
        site, cash_out = crew.site, crew.cash_out
        card = next(card for card in victim.cards if card.card_type is CardType.DEBIT)
        skimmed_at = site.skim_time(rng)
        amount = atm_amount(rng, victim.customer.income_band)
        params = _params(card, site.atm, skimmed_at, cash_out)
        params |= {"site_opens": site.opens.isoformat(), "site_closes": site.closes.isoformat()}
        return CounterfeitEpisode(
            scenario_id=scenario_id,
            victim_customer_id=victim.customer.customer_id,
            params=params,
            card=card,
            skim=atm_withdrawal_draft(card, site.atm, amount, skimmed_at),
            rounds=cash_out.rounds(delay),
            atms=cash_out.atms,
            shops=cash_out.shops,
            seed=rng.getrandbits(32),
        )

    def _plan_shop_skim(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> CounterfeitEpisode:
        """A credit card skimmed at a fuel station or restaurant near home, then cloned alone."""
        customer = victim.customer
        card = _credit_card(victim)
        if card is None:
            raise SimulationError(f"{customer.customer_id} has no credit card to skim at a shop")
        category = rng.choice(CREDIT_SKIM_CATEGORIES)
        shop = rng.choice(self._nearby(customer, category, NEARBY_MERCHANTS))
        first_day = _sa_day(start)
        skim_day = first_day - timedelta(days=rng.randint(*FIRST_USE_DAYS))
        skimmed_at = pick_event_time(rng, skim_day)
        amount = card_amount(rng, category, customer.income_band)
        cash_out = self._plan_cash_out(rng, customer, first_day)
        return CounterfeitEpisode(
            scenario_id=scenario_id,
            victim_customer_id=customer.customer_id,
            params=_params(card, shop, skimmed_at, cash_out),
            card=card,
            skim=store_purchase_draft(card, shop, amount, skimmed_at, False),
            rounds=cash_out.rounds(timedelta()),
            atms=cash_out.atms,
            shops=cash_out.shops,
            seed=rng.getrandbits(32),
        )

    def _plan_cash_out(self, rng: random.Random, customer: Customer, first_day: date) -> CashOut:
        """Another city than the victim's, the days there, and the ATMs and shops the crew uses."""
        city = _cash_out_city(rng, customer.home_city)
        away = away_customer(customer, city)
        days = rng.randint(*DAYS_USED)
        return CashOut(
            city=city,
            day_starts=tuple(_set_out(rng, first_day + timedelta(days=n)) for n in range(days)),
            atms=self._nearby(away, MerchantCategory.ATM, CREW_ATMS),
            shops=tuple(
                shop for c in PURCHASE_CENTS for shop in self._nearby(away, c, NEARBY_MERCHANTS)
            ),
        )

    def _nearby(
        self, customer: Customer, category: MerchantCategory, count: int
    ) -> tuple[Merchant, ...]:
        nearby = self._index.nearest(customer, category, count)
        if not nearby:
            raise SimulationError(f"no {category} merchants for counterfeit card fraud")
        return tuple(nearby)


def _credit_card(victim: Victim) -> Card | None:
    return next((card for card in victim.cards if card.card_type is CardType.CREDIT), None)


def _params(
    card: Card, skim_point: Merchant, skimmed_at: datetime, cash_out: CashOut
) -> dict[str, ParamValue]:
    """What every episode records: the skim, and when and where the clone is used."""
    first_use = _sa_day(cash_out.day_starts[0]) - _sa_day(skimmed_at)
    return {
        "card_type": card.card_type.value,
        "skimmed_at": skimmed_at.isoformat(),
        "skim_merchant_id": skim_point.merchant_id,
        "cash_out_city": cash_out.city.name,
        "first_use_after_days": first_use.days,
        "days_used": len(cash_out.day_starts),
    }


def _set_out(rng: random.Random, day: date) -> datetime:
    """When the crew sets out on ``day``, in shop hours."""
    opens = datetime.combine(day, ROUND_HOURS[0], tzinfo=SAST)
    closes = datetime.combine(day, ROUND_HOURS[1], tzinfo=SAST)
    return random_datetime_between(rng, opens, closes).astimezone(UTC)


def _midnight_after(moment: datetime) -> datetime:
    midnight = datetime.combine(_sa_day(moment) + timedelta(days=1), time(), tzinfo=SAST)
    return midnight.astimezone(UTC)


def _cash_out_city(rng: random.Random, home_city: str) -> City:
    """Another city, more often a big one where strangers go unnoticed."""
    return pick(rng, {city: city.weight for city in CITIES if city.name != home_city})


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
