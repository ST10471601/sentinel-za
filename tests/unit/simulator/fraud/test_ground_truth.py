import json
from datetime import UTC, datetime, timedelta

from sentinel.domain.balance_enquiries import BalanceEnquiry
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
from sentinel.simulator.drafts import (
    EnquiryDraft,
    Settlement,
    TransactionDraft,
    to_balance_enquiry,
    to_transaction,
)
from sentinel.simulator.fraud.episodes import ScriptedEpisode
from sentinel.simulator.fraud.ground_truth import (
    GroundTruth,
    normal_enquiry_label,
    normal_labels,
)

START = datetime(2026, 2, 3, 20, 0, tzinfo=UTC)
REPORTED_AT = START + timedelta(days=5)
APPROVED = Settlement(TransactionStatus.APPROVED, None, 100_000)
DECLINED = Settlement(TransactionStatus.DECLINED, DeclineReason.LIMIT_EXCEEDED, 100_000)


def draft(number: int, minutes: int) -> TransactionDraft:
    return TransactionDraft(
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


def transaction(number: int, minutes: int, settlement: Settlement = APPROVED) -> Transaction:
    return to_transaction(draft(number, minutes), number, settlement)


def enquiry(number: int, minutes: int) -> BalanceEnquiry:
    draft = EnquiryDraft(
        card_id="CRD-0000001",
        account_id="ACC-0000001",
        merchant_id="MER-0000002",
        event_time=START + timedelta(minutes=minutes),
        terminal_lat=-26.2,
        terminal_lon=28.0,
    )
    return to_balance_enquiry(draft, number, APPROVED)


def episode(scenario_id: str) -> ScriptedEpisode:
    return ScriptedEpisode(
        scenario_id=scenario_id,
        fraud_type=FraudType.CARD_NOT_PRESENT,
        victim_customer_id="CUS-0000001",
        params={"foreign": True, "charges": 2},
        drafts=(draft(1, 0),),
    )


def test_episode_is_summarised_and_every_transaction_labelled() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    truth.record("SCN-0000001", [transaction(1, 0), transaction(2, 30, DECLINED)])
    truth.record("SCN-0000001", [transaction(3, 45)])

    rows = truth.finish()
    instances, labels = rows.scenarios, rows.transaction_labels

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
    for label in labels:
        assert label.is_fraud
        assert label.scenario_id == "SCN-0000001"
        assert label.reported_at == REPORTED_AT  # one claim covers the episode


def test_unreported_fraud_has_no_reported_at() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), reported_at=None)
    truth.record("SCN-0000001", [transaction(1, 0)])

    labels = truth.finish().transaction_labels
    assert labels[0].is_fraud
    assert labels[0].reported_at is None


def test_episodes_that_never_happened_are_left_out() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    truth.add_episode(episode("SCN-0000002"), REPORTED_AT)
    truth.record("SCN-0000002", [transaction(1, 0)])

    instances = truth.finish().scenarios
    assert [i.scenario_id for i in instances] == ["SCN-0000002"]


def test_normal_transactions_get_clean_labels() -> None:
    (label,) = normal_labels([transaction(1, 0)])
    assert not label.is_fraud
    assert label.fraud_type is None
    assert label.reported_at is None


def test_a_fraudsters_balance_check_is_labelled_and_can_start_the_episode() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    truth.record_enquiry("SCN-0000001", enquiry(1, -2))
    truth.record("SCN-0000001", [transaction(1, 0)])

    rows = truth.finish()

    assert rows.scenarios[0].started_at == START - timedelta(minutes=2)
    (label,) = rows.enquiry_labels
    assert (label.balance_enquiry_id, label.is_fraud) == ("BEQ-000000001", True)
    assert (label.scenario_id, label.reported_at) == ("SCN-0000001", REPORTED_AT)


def test_normal_balance_checks_get_clean_labels() -> None:
    label = normal_enquiry_label(enquiry(1, 0))
    assert not label.is_fraud
    assert (label.fraud_type, label.scenario_id, label.reported_at) == (None, None, None)
