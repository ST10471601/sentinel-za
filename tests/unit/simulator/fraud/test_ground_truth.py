import json
import random
from datetime import UTC, datetime, timedelta

from sentinel.domain.labels import FraudType
from sentinel.domain.transactions import (
    Channel,
    DeclineReason,
    Direction,
    EntryMode,
    Transaction,
    TransactionAuthMethod,
    TransactionStatus,
)
from sentinel.simulator.drafts import Settlement, TransactionDraft, to_transaction
from sentinel.simulator.fraud.episodes import ScriptedEpisode
from sentinel.simulator.fraud.ground_truth import REPORT_DELAY_DAYS, GroundTruth, normal_labels

START = datetime(2026, 2, 3, 20, 0, tzinfo=UTC)
APPROVED = Settlement(TransactionStatus.APPROVED, None, 100_000)
DECLINED = Settlement(TransactionStatus.DECLINED, DeclineReason.LIMIT_EXCEEDED, 100_000)


def transaction(number: int, minutes: int, settlement: Settlement = APPROVED) -> Transaction:
    draft = TransactionDraft(
        account_id="ACC-0000001",
        direction=Direction.DEBIT,
        amount_cents=10_000 * number,
        event_time=START + timedelta(minutes=minutes),
        channel=Channel.CARD_NOT_PRESENT,
        auth_method=TransactionAuthMethod.THREE_DS,
        country_code="ZA",
        card_id="CRD-0000001",
        merchant_id="MER-0000001",
        entry_mode=EntryMode.ECOMMERCE,
    )
    return to_transaction(draft, number, settlement)


def episode(scenario_id: str) -> ScriptedEpisode:
    return ScriptedEpisode(
        scenario_id=scenario_id,
        fraud_type=FraudType.CARD_NOT_PRESENT,
        victim_customer_id="CUS-0000001",
        params={"foreign": True, "charges": 2},
        drafts=(),
    )


def test_episode_is_summarised_and_every_transaction_labelled() -> None:
    truth = GroundTruth(random.Random(1), unreported_share=0.0)
    truth.add_episode(episode("SCN-0000001"))
    truth.record("SCN-0000001", [transaction(1, 0), transaction(2, 30, DECLINED)])
    truth.record("SCN-0000001", [transaction(3, 45)])

    instances, labels = truth.finish()

    (instance,) = instances
    assert instance.started_at == START
    assert instance.ended_at == START + timedelta(minutes=45)
    assert instance.total_amount_cents == 10_000 + 30_000  # the declined attempt stole nothing
    assert json.loads(instance.params) == {"charges": 2, "foreign": True}

    assert [label.transaction_id for label in labels] == [
        "TXN-000000001",
        "TXN-000000002",
        "TXN-000000003",
    ]
    low, high = REPORT_DELAY_DAYS[FraudType.CARD_NOT_PRESENT]
    for label in labels:
        assert label.is_fraud
        assert label.scenario_id == "SCN-0000001"
        assert label.reported_at == labels[0].reported_at  # one claim covers the episode
        assert label.reported_at is not None
        delay = label.reported_at - instance.ended_at
        assert timedelta(days=low) <= delay <= timedelta(days=high)


def test_unreported_fraud_has_no_reported_at() -> None:
    truth = GroundTruth(random.Random(1), unreported_share=1.0)
    truth.add_episode(episode("SCN-0000001"))
    truth.record("SCN-0000001", [transaction(1, 0)])

    _, labels = truth.finish()
    assert labels[0].is_fraud
    assert labels[0].reported_at is None


def test_episodes_that_never_happened_are_left_out() -> None:
    truth = GroundTruth(random.Random(1), unreported_share=0.0)
    truth.add_episode(episode("SCN-0000001"))
    truth.add_episode(episode("SCN-0000002"))
    truth.record("SCN-0000002", [transaction(1, 0)])

    instances, _ = truth.finish()
    assert [i.scenario_id for i in instances] == ["SCN-0000002"]


def test_normal_transactions_get_clean_labels() -> None:
    (label,) = normal_labels([transaction(1, 0)])
    assert not label.is_fraud
    assert label.fraud_type is None
    assert label.reported_at is None
