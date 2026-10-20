import random
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.domain.labels import FraudType
from sentinel.domain.transactions import Channel, Direction, TransactionAuthMethod
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.episodes import ScriptedEpisode
from sentinel.simulator.fraud.reports import REPORT_DELAY_DAYS, plan_reported_at

END = datetime(2026, 2, 3, 20, 0, tzinfo=UTC)


def episode(fraud_type: FraudType) -> ScriptedEpisode:
    attempt = TransactionDraft(
        account_id="ACC-0000001",
        direction=Direction.DEBIT,
        amount_cents=5_000,
        event_time=END,
        channel=Channel.CARD_NOT_PRESENT,
        auth_method=TransactionAuthMethod.THREE_DS,
        country_code="US",
        card_id="CRD-0000001",
    )
    return ScriptedEpisode("SCN-0000001", fraud_type, "CUS-0000001", {}, (attempt,))


def test_every_fraud_type_has_a_report_delay() -> None:
    assert set(REPORT_DELAY_DAYS) == set(FraudType)


@pytest.mark.parametrize("fraud_type", list(FraudType))
def test_reports_land_within_the_delay_after_the_planned_end(fraud_type: FraudType) -> None:
    rng = random.Random(1)
    low, high = REPORT_DELAY_DAYS[fraud_type]

    for _ in range(50):
        reported_at = plan_reported_at(rng, episode(fraud_type), unreported_share=0.0)
        assert reported_at is not None
        assert timedelta(days=low) <= reported_at - END <= timedelta(days=high)


def test_unreported_fraud_has_no_report() -> None:
    reported_at = plan_reported_at(
        random.Random(1), episode(FraudType.CARD_NOT_PRESENT), unreported_share=1.0
    )
    assert reported_at is None
