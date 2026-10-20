"""Blocking cards and issuing replacements, as a bank does when card fraud is reported."""

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sentinel.core.datetimes import SAST, add_years, to_sast
from sentinel.domain.accounts import Card, CardEvent, CardEventType, CardStatus
from sentinel.simulator.accounts import CARD_VALIDITY_YEARS
from sentinel.simulator.identifiers import IdSequence, make_card_token, make_pan_last4
from sentinel.simulator.randomness import random_datetime_between

# Simulation assumptions: days from the block until the new card is in hand, and the hours
# in which a branch hands it over or a courier delivers it.
REPLACEMENT_DAYS = (2, 7)
HANDOVER_HOURS = (time(9), time(16))


@dataclass(frozen=True, slots=True)
class CardBlock:
    """The bank blocks a card at a set time. Every later attempt on it is declined."""

    card_id: str
    blocked_at: datetime
    event_type: CardEventType  # blocked, or reported_lost


@dataclass(frozen=True, slots=True)
class Replacement:
    """A new card for one that was blocked, and the event that records it."""

    replaced_card_id: str
    card: Card
    event: CardEvent


class CardReplacements:
    """Records card blocks and issues each blocked card's replacement a few days later."""

    def __init__(
        self,
        rng: random.Random,
        cards: Sequence[Card],
        card_ids: IdSequence,
        event_ids: IdSequence,
    ) -> None:
        self._rng = rng
        self._cards = {card.card_id: card for card in cards}
        self._card_ids = card_ids
        self._event_ids = event_ids
        self._due: dict[date, list[str]] = defaultdict(list)  # SA day -> cards to replace

    def block(self, block: CardBlock) -> CardEvent:
        """Record the block and schedule the card's replacement."""
        blocked_day = to_sast(block.blocked_at).date()
        issue_day = blocked_day + timedelta(days=self._rng.randint(*REPLACEMENT_DAYS))
        self._due[issue_day].append(block.card_id)
        return CardEvent(
            card_event_id=self._event_ids.next_id(),
            card_id=block.card_id,
            event_type=block.event_type,
            event_time=block.blocked_at,
            replaces_card_id=None,
        )

    def issue_due(self, day: date) -> list[Replacement]:
        """Issue the replacements due on SA calendar day ``day``."""
        return [self._issue(card_id, day) for card_id in self._due.pop(day, [])]

    def _issue(self, old_card_id: str, day: date) -> Replacement:
        """Same account, type and limits; a new number."""
        old = self._cards[old_card_id]
        opens = datetime.combine(day, HANDOVER_HOURS[0], tzinfo=SAST)
        closes = datetime.combine(day, HANDOVER_HOURS[1], tzinfo=SAST)
        issued_at = random_datetime_between(self._rng, opens, closes).astimezone(UTC)
        card = Card(
            card_id=self._card_ids.next_id(),
            account_id=old.account_id,
            card_type=old.card_type,
            pan_token=make_card_token(self._rng),
            pan_last4=make_pan_last4(self._rng),
            issued_at=issued_at,
            expires_on=add_years(issued_at.date(), CARD_VALIDITY_YEARS),
            status=CardStatus.ACTIVE,
            daily_atm_limit_cents=old.daily_atm_limit_cents,
            daily_pos_limit_cents=old.daily_pos_limit_cents,
        )
        self._cards[card.card_id] = card  # a replacement can be blocked too
        event = CardEvent(
            card_event_id=self._event_ids.next_id(),
            card_id=card.card_id,
            event_type=CardEventType.ISSUED,
            event_time=issued_at,
            replaces_card_id=old_card_id,
        )
        return Replacement(old_card_id, card, event)
