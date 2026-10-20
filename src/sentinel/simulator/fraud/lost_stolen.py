"""Lost and stolen cards: a physical card taken from the victim and used before it is blocked.

Shape (SABRIC 2025): the card is often taken at an ATM by card swap or distraction, with the
PIN shoulder-surfed, then used quickly for cash and purchases before the victim reports it.
ATM withdrawals are 49.3% of debit card losses; supermarkets, ATMs and tollgates lead for both
card types.
"""

import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, to_sast
from sentinel.core.errors import SimulationError
from sentinel.domain.accounts import Card, CardType
from sentinel.domain.customers import Customer
from sentinel.domain.labels import FraudType
from sentinel.domain.merchants import Merchant, MerchantCategory
from sentinel.simulator.card_activity import (
    BALANCE_CHECK_SECONDS,
    HOME_COUNTRY,
    MerchantIndex,
    atm_withdrawal_draft,
    balance_enquiry_draft,
    online_purchase_draft,
    store_purchase_draft,
)
from sentinel.simulator.drafts import EnquiryDraft, TransactionDraft
from sentinel.simulator.fraud.episodes import (
    Balances,
    CardLoss,
    FraudAction,
    FraudActions,
    ParamValue,
    Victim,
)
from sentinel.simulator.randomness import chance, log_uniform_int, random_datetime_between
from sentinel.simulator.spending import ATM_STEP_CENTS, atm_amount

DEBIT_CARD_SHARE = 0.88  # debit losses R253.0m vs credit R34.2m
# Share of losses abroad, used as the share of episodes with online use abroad.
ONLINE_ABROAD_SHARE: dict[CardType, float] = {CardType.DEBIT: 0.235, CardType.CREDIT: 0.41}

# Simulation assumptions from here on.
PIN_KNOWN_SHARE = 0.7  # taken at an ATM with the PIN seen; otherwise lost or pickpocketed
THIEF_BALANCE_CHECK_SHARE = 0.8  # checks what there is to take before the first withdrawal
THEFT_HOURS = (time(8), time(18))
VICTIM_WITHDRAWAL_LEAD_SECONDS = (30, 180)  # the victim's own withdrawal, just before the swap
FIRST_USE_SECONDS = (300, 2_700)  # 5 to 45 minutes after the theft
CALL_SECONDS = (3_600, 43_200)  # the victim calls the bank 1 to 12 hours after the theft
MIDNIGHT_ROUND_SECONDS = (60, 900)  # just after midnight, when daily limits reset
RETRY_GAP_SECONDS = (120, 1_800)
RETRIES = (1, 2)  # tries after the block, which decline

NEARBY_MERCHANTS = 6  # the thief stays close to where the card was taken
ATMS_PER_ROUND = (2, 4)
WITHDRAWALS_PER_ATM = (1, 3)
CASH_CENTS = (100_000, 300_000)  # R1,000 to R3,000 per withdrawal
ATM_GAP_SECONDS = (30, 180)  # between withdrawals at one ATM
DRIVE_SECONDS = (300, 1_500)  # between ATMs and shops
PIN_PURCHASES = (1, 3)
PIN_PURCHASE_CENTS = (20_000, 250_000)
TAPS = (4, 12)
TAP_CENTS = (10_000, 49_000)  # kept under the R500 no-PIN contactless limit
TAP_GAP_SECONDS = (120, 900)
ONLINE_PURCHASES = (1, 3)
ONLINE_CENTS = (30_000, 500_000)
ONLINE_GAP_SECONDS = (60, 600)

PIN_SHOP_CATEGORIES = (MerchantCategory.GROCERY, MerchantCategory.TOLL)
TAP_CATEGORIES = (MerchantCategory.GROCERY, MerchantCategory.FUEL, MerchantCategory.RESTAURANT)
ONLINE_ABROAD_CATEGORIES = frozenset(
    {MerchantCategory.ONLINE_RETAIL, MerchantCategory.DIGITAL_GOODS, MerchantCategory.TRAVEL_AGENCY}
)


@dataclass(frozen=True, slots=True)
class Round:
    """A spree between two times. How much it takes depends on the balance when it starts."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class LostStolenEpisode:
    """A stolen card used in rounds until the victim's call blocks it, then tried once or twice."""

    scenario_id: str
    victim_customer_id: str
    params: Mapping[str, ParamValue]
    card: Card
    card_loss: CardLoss
    is_pin_known: bool
    rounds: tuple[Round, ...]
    scripted: tuple[TransactionDraft, ...]  # online use abroad, and the tries after the block
    victim_withdrawal: TransactionDraft | None  # where the card was swapped, labelled not fraud
    atms: tuple[Merchant, ...]
    shops: tuple[Merchant, ...]
    seed: int  # each round draws from its own stream, so the order days are played can't matter

    fraud_type = FraudType.CARD_PRESENT_LOST_STOLEN

    @property
    def card_id(self) -> str:
        """The stolen card."""
        return self.card.card_id

    def days(self) -> frozenset[date]:
        """SA calendar days on which the thief acts, or the victim's withdrawal happens."""
        times = [spree.start for spree in self.rounds]
        times += [draft.event_time for draft in self.scripted]
        if self.victim_withdrawal is not None:
            times.append(self.victim_withdrawal.event_time)
        return frozenset(_sa_day(moment) for moment in times)

    def planned_end(self) -> datetime:
        """The thief's last try, after the block."""
        return max(draft.event_time for draft in self.scripted)

    def act(self, day: date, balances: Balances) -> FraudActions:
        """The victim's withdrawal, the rounds that start on ``day`` and any scripted attempts."""
        actions = FraudActions()
        withdrawal = self.victim_withdrawal
        if withdrawal is not None and _sa_day(withdrawal.event_time) == day:
            actions.genuine.append(withdrawal)
        for spree in self.rounds:
            if _sa_day(spree.start) == day:
                available = balances.available_cents(self.card.account_id)
                actions.attempts += self._play(spree, available)
        actions.attempts += [d for d in self.scripted if _sa_day(d.event_time) == day]
        return actions

    def _play(self, spree: Round, available_cents: int) -> list[FraudAction]:
        rng = random.Random(f"{self.seed}:{spree.start.isoformat()}")
        if self.is_pin_known:
            return self._take_cash_then_shop(rng, spree, available_cents)
        return self._tap(rng, spree, available_cents)

    def _take_cash_then_shop(
        self, rng: random.Random, spree: Round, available_cents: int
    ) -> list[FraudAction]:
        """Withdraw until the money or the daily ATM limit runs out, then buy with chip and PIN."""
        target = min(available_cents, self.card.daily_atm_limit_cents)
        drafts: list[FraudAction] = []
        taken = 0
        moment = spree.start
        for number, (atm, moment) in enumerate(self._atm_visits(rng, spree.start)):
            is_first = number == 0
            # The withdrawal that goes past the target is the one that declines.
            if moment >= spree.end or (not is_first and taken >= target):
                break
            if is_first and chance(rng, THIEF_BALANCE_CHECK_SHARE):
                drafts.append(_balance_check(rng, self.card, atm, moment))
            amount = _cash_cents(rng)
            drafts.append(atm_withdrawal_draft(self.card, atm, amount, moment))
            taken += amount

        for _ in range(rng.randint(*PIN_PURCHASES)):
            moment += _seconds(rng, DRIVE_SECONDS)
            if moment >= spree.end:
                break
            amount = rng.randint(*PIN_PURCHASE_CENTS)
            shop = rng.choice(self.shops)
            drafts.append(store_purchase_draft(self.card, shop, amount, moment, False))
        return drafts

    def _atm_visits(
        self, rng: random.Random, start: datetime
    ) -> Iterator[tuple[Merchant, datetime]]:
        """A few nearby ATMs, one after another, with one to three withdrawals at each."""
        moment = start
        count = min(rng.randint(*ATMS_PER_ROUND), len(self.atms))
        for atm in rng.sample(self.atms, count):
            for _ in range(rng.randint(*WITHDRAWALS_PER_ATM)):
                yield atm, moment
                moment += _seconds(rng, ATM_GAP_SECONDS)
            moment += _seconds(rng, DRIVE_SECONDS)

    def _tap(self, rng: random.Random, spree: Round, available_cents: int) -> list[FraudAction]:
        """Contactless taps under the no-PIN limit until one declines."""
        target = min(available_cents, self.card.daily_pos_limit_cents)
        drafts: list[FraudAction] = []
        spent = 0
        moment = spree.start
        for _ in range(rng.randint(*TAPS)):
            if moment >= spree.end or (drafts and spent >= target):
                break
            amount = rng.randint(*TAP_CENTS)
            drafts.append(
                store_purchase_draft(self.card, rng.choice(self.shops), amount, moment, True)
            )
            spent += amount
            moment += _seconds(rng, TAP_GAP_SECONDS)
        return drafts


class LostStolen:
    """Plans lost and stolen card episodes: theft, a spree or two, and the victim's call."""

    fraud_type = FraudType.CARD_PRESENT_LOST_STOLEN

    def __init__(self, merchants: Sequence[Merchant]) -> None:
        self._index = MerchantIndex(merchants)
        self._online_abroad = [
            merchant
            for merchant in merchants
            if merchant.is_online
            and merchant.country_code != HOME_COUNTRY
            and merchant.category in ONLINE_ABROAD_CATEGORIES
        ]
        if not self._online_abroad:
            raise SimulationError("no foreign online merchants for lost or stolen card fraud")

    def can_target(self, victim: Victim) -> bool:
        """Every customer carries a debit card."""
        return any(card.card_type is CardType.DEBIT for card in victim.cards)

    def plan(
        self, rng: random.Random, victim: Victim, start: datetime, scenario_id: str
    ) -> LostStolenEpisode:
        """Plan the theft, the rounds before the victim's call and the tries after it."""
        card = _pick_card(rng, victim.cards)
        is_pin_known = chance(rng, PIN_KNOWN_SHARE)
        lost_at = _theft_time(rng, _sa_day(start))
        reported_lost_at = lost_at + _seconds(rng, CALL_SECONDS)
        rounds = _plan_rounds(rng, lost_at, reported_lost_at)

        atms = self._nearby(victim.customer, MerchantCategory.ATM)
        theft_atm = rng.choice(atms)
        thief_atms = tuple(atm for atm in atms if atm != theft_atm)
        shops = self._shops(victim.customer, is_pin_known)

        is_online_abroad = chance(rng, ONLINE_ABROAD_SHARE[card.card_type])
        online = self._plan_online(rng, card, rounds[0].start) if is_online_abroad else []
        retries = _plan_retries(rng, card, reported_lost_at, thief_atms, shops, is_pin_known)

        victim_withdrawal = None
        params: dict[str, ParamValue] = {}
        if is_pin_known:
            lead = _seconds(rng, VICTIM_WITHDRAWAL_LEAD_SECONDS)
            amount = atm_amount(rng, victim.customer.income_band)
            victim_withdrawal = atm_withdrawal_draft(card, theft_atm, amount, lost_at - lead)
            params["theft_atm_id"] = theft_atm.merchant_id

        params |= {
            "card_type": card.card_type.value,
            "lost_at": lost_at.isoformat(),  # the bank never sees this; only the call
            "pin_known": is_pin_known,
            "call_delay_minutes": int((reported_lost_at - lost_at).total_seconds() // 60),
            "midnight_round": len(rounds) > 1,
            "online_abroad": is_online_abroad,
            "retries": len(retries),
        }
        return LostStolenEpisode(
            scenario_id=scenario_id,
            victim_customer_id=victim.customer.customer_id,
            params=params,
            card=card,
            card_loss=CardLoss(card.card_id, lost_at, reported_lost_at),
            is_pin_known=is_pin_known,
            rounds=rounds,
            scripted=tuple(online + retries),
            victim_withdrawal=victim_withdrawal,
            atms=thief_atms,
            shops=shops,
            seed=rng.getrandbits(32),
        )

    def _nearby(self, customer: Customer, category: MerchantCategory) -> tuple[Merchant, ...]:
        # One toll plaza: the one on the way out of town.
        count = 1 if category is MerchantCategory.TOLL else NEARBY_MERCHANTS
        nearby = self._index.nearest(customer, category, count)
        if not nearby:
            raise SimulationError(f"no {category} merchants for lost or stolen card fraud")
        return tuple(nearby)

    def _shops(self, customer: Customer, is_pin_known: bool) -> tuple[Merchant, ...]:
        """Supermarkets and a tollgate with the PIN; small taps at everyday shops without it."""
        categories = PIN_SHOP_CATEGORIES if is_pin_known else TAP_CATEGORIES
        return tuple(shop for c in categories for shop in self._nearby(customer, c))

    def _plan_online(
        self, rng: random.Random, card: Card, start: datetime
    ) -> list[TransactionDraft]:
        """A few online purchases abroad, made with the details printed on the card."""
        drafts: list[TransactionDraft] = []
        moment = start
        for _ in range(rng.randint(*ONLINE_PURCHASES)):
            moment += _seconds(rng, ONLINE_GAP_SECONDS)
            amount = log_uniform_int(rng, ONLINE_CENTS)
            merchant = rng.choice(self._online_abroad)
            drafts.append(online_purchase_draft(card, merchant, amount, moment))
        return drafts


def _pick_card(rng: random.Random, cards: Sequence[Card]) -> Card:
    debit = next(card for card in cards if card.card_type is CardType.DEBIT)
    credit = next((card for card in cards if card.card_type is CardType.CREDIT), None)
    if credit is None or chance(rng, DEBIT_CARD_SHARE):
        return debit
    return credit


def _theft_time(rng: random.Random, day: date) -> datetime:
    """Daytime, when people use ATMs and carry their wallets about."""
    opens = datetime.combine(day, THEFT_HOURS[0], tzinfo=SAST)
    closes = datetime.combine(day, THEFT_HOURS[1], tzinfo=SAST)
    return random_datetime_between(rng, opens, closes).astimezone(UTC)


def _plan_rounds(rng: random.Random, lost_at: datetime, call: datetime) -> tuple[Round, ...]:
    """A round straight after the theft, and another after midnight if the call comes late."""
    midnight = datetime.combine(_sa_day(lost_at) + timedelta(days=1), time(), tzinfo=SAST)
    first = Round(lost_at + _seconds(rng, FIRST_USE_SECONDS), min(call, midnight))
    if call <= midnight + timedelta(seconds=MIDNIGHT_ROUND_SECONDS[1]):
        return (first,)
    return first, Round(midnight + _seconds(rng, MIDNIGHT_ROUND_SECONDS), call)


def _plan_retries(
    rng: random.Random,
    card: Card,
    call: datetime,
    atms: Sequence[Merchant],
    shops: Sequence[Merchant],
    is_pin_known: bool,
) -> list[TransactionDraft]:
    """The thief doesn't know the card is blocked yet and tries again."""
    drafts: list[TransactionDraft] = []
    moment = call
    for _ in range(rng.randint(*RETRIES)):
        moment += _seconds(rng, RETRY_GAP_SECONDS)
        if is_pin_known:
            drafts.append(atm_withdrawal_draft(card, rng.choice(atms), _cash_cents(rng), moment))
        else:
            amount = rng.randint(*TAP_CENTS)
            drafts.append(store_purchase_draft(card, rng.choice(shops), amount, moment, True))
    return drafts


def _balance_check(
    rng: random.Random, card: Card, atm: Merchant, withdrawn_at: datetime
) -> EnquiryDraft:
    """A balance check at the ATM, a moment before the withdrawal."""
    return balance_enquiry_draft(card, atm, withdrawn_at - _seconds(rng, BALANCE_CHECK_SECONDS))


def _cash_cents(rng: random.Random) -> int:
    return rng.randint(*CASH_CENTS) // ATM_STEP_CENTS * ATM_STEP_CENTS


def _seconds(rng: random.Random, bounds: tuple[int, int]) -> timedelta:
    return timedelta(seconds=rng.randint(*bounds))


def _sa_day(moment: datetime) -> date:
    return to_sast(moment).date()
