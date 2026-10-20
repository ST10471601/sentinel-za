import random
from datetime import UTC, date, datetime, time, timedelta

import pytest

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Card, CardEventType, CardStatus
from sentinel.simulator.card_replacement import (
    HANDOVER_HOURS,
    REPLACEMENT_DAYS,
    CardBlock,
    CardReplacements,
)
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.reference_data import ReferenceData

BLOCKED_AT = datetime(2026, 3, 6, 21, 30, tzinfo=UTC)  # 23:30 on 6 March in SA


@pytest.fixture
def replacements(reference_data: ReferenceData) -> CardReplacements:
    card_ids = IdSequence("CRD", start_after=len(reference_data.cards))
    return CardReplacements(random.Random(1), reference_data.cards, card_ids, IdSequence("CEV"))


def issue_all(replacements: CardReplacements) -> list[tuple[date, Card]]:
    """Every card issued over the next ten days, with the SA day it was due."""
    issued = []
    for offset in range(10):
        day = date(2026, 3, 6) + timedelta(days=offset)
        issued += [(day, r.card) for r in replacements.issue_due(day)]
    return issued


def test_a_block_is_recorded_as_an_event_at_the_block_time(
    replacements: CardReplacements, reference_data: ReferenceData
) -> None:
    card = reference_data.cards[0]
    event = replacements.block(CardBlock(card.card_id, BLOCKED_AT, CardEventType.BLOCKED))

    assert (event.card_event_id, event.card_id) == ("CEV-0000001", card.card_id)
    assert event.event_type is CardEventType.BLOCKED
    assert event.event_time == BLOCKED_AT
    assert event.replaces_card_id is None


def test_the_replacement_matches_the_old_card_but_has_a_new_number(
    replacements: CardReplacements, reference_data: ReferenceData
) -> None:
    old = reference_data.cards[0]
    replacements.block(CardBlock(old.card_id, BLOCKED_AT, CardEventType.BLOCKED))

    ((_, card),) = issue_all(replacements)
    assert card.card_id == f"CRD-{len(reference_data.cards) + 1:07d}"
    assert (card.account_id, card.card_type) == (old.account_id, old.card_type)
    assert (card.daily_atm_limit_cents, card.daily_pos_limit_cents) == (
        old.daily_atm_limit_cents,
        old.daily_pos_limit_cents,
    )
    assert card.pan_token != old.pan_token
    assert card.status is CardStatus.ACTIVE


def test_replacements_arrive_days_later_in_handover_hours(
    replacements: CardReplacements, reference_data: ReferenceData
) -> None:
    for card in reference_data.cards[:40]:
        replacements.block(CardBlock(card.card_id, BLOCKED_AT, CardEventType.BLOCKED))

    issued = issue_all(replacements)
    assert len(issued) == 40
    low, high = REPLACEMENT_DAYS
    for day, card in issued:
        assert low <= (day - date(2026, 3, 6)).days <= high  # counted from the SA block day
        local = to_sast(card.issued_at)
        assert local.date() == day
        assert HANDOVER_HOURS[0] <= local.time() <= HANDOVER_HOURS[1]


def test_issued_event_points_back_at_the_old_card(
    replacements: CardReplacements, reference_data: ReferenceData
) -> None:
    old = reference_data.cards[0]
    replacements.block(CardBlock(old.card_id, BLOCKED_AT, CardEventType.BLOCKED))
    for offset in range(10):
        for replacement in replacements.issue_due(date(2026, 3, 6) + timedelta(days=offset)):
            assert replacement.replaced_card_id == old.card_id
            assert replacement.event.event_type is CardEventType.ISSUED
            assert replacement.event.card_id == replacement.card.card_id
            assert replacement.event.replaces_card_id == old.card_id
            assert replacement.event.event_time == replacement.card.issued_at


def test_a_replacement_can_itself_be_replaced(
    replacements: CardReplacements, reference_data: ReferenceData
) -> None:
    replacements.block(
        CardBlock(reference_data.cards[0].card_id, BLOCKED_AT, CardEventType.BLOCKED)
    )
    ((_, first),) = issue_all(replacements)
    later = datetime.combine(date(2026, 3, 20), time(10), tzinfo=UTC)
    replacements.block(CardBlock(first.card_id, later, CardEventType.BLOCKED))

    second = [r.card for d in range(20, 30) for r in replacements.issue_due(date(2026, 3, d))]
    assert [card.account_id for card in second] == [first.account_id]
