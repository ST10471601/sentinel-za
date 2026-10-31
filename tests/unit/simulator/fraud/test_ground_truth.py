import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sentinel.domain.balance_enquiries import BalanceEnquiry
from sentinel.domain.customers import BankingChannel
from sentinel.domain.labels import FraudType, LoginSessionLabel, SimSwapLabel
from sentinel.domain.sessions import LoginSession, SessionAuthMethod
from sentinel.domain.sim_swaps import SimSwapEvent
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
    normal_session_labels,
    normal_sim_swap_labels,
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


def session(number: int, minutes: int) -> LoginSession:
    return LoginSession(
        session_id=f"SES-{number:07d}",
        customer_id="CUS-0000001",
        device_id="DEV-0000009",
        channel=BankingChannel.APP,
        started_at=START + timedelta(minutes=minutes),
        auth_method=SessionAuthMethod.OTP,
        ip_country="ZA",
        lat=None,
        lon=None,
        remote_access_detected=False,
    )


def sim_swap(number: int, minutes: int) -> SimSwapEvent:
    swapped_at = START + timedelta(minutes=minutes)
    return SimSwapEvent(
        sim_swap_id=f"SIM-{number:07d}",
        customer_id="CUS-0000001",
        phone_number="+27821234567",
        swapped_at=swapped_at,
        notified_at=swapped_at + timedelta(hours=3),
        network="MNO-1",
    )


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


def test_a_fraudsters_logins_and_sim_swaps_share_the_episodes_report() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    truth.record_sim_swaps("SCN-0000001", [sim_swap(1, -30)])
    truth.record_sessions("SCN-0000001", [session(1, -20), session(2, -5)])
    truth.record("SCN-0000001", [transaction(1, 0)])

    rows = truth.finish()

    assert rows.scenarios[0].started_at == START - timedelta(minutes=30)  # starts at the swap
    labels: list[LoginSessionLabel | SimSwapLabel] = [*rows.session_labels, *rows.sim_swap_labels]
    assert [label.session_id for label in rows.session_labels] == ["SES-0000001", "SES-0000002"]
    assert [label.sim_swap_id for label in rows.sim_swap_labels] == ["SIM-0000001"]
    for label in labels:
        assert (label.is_fraud, label.fraud_type) == (True, FraudType.CARD_NOT_PRESENT)
        assert (label.scenario_id, label.reported_at) == ("SCN-0000001", REPORTED_AT)


def test_an_episode_cut_off_after_its_sim_swap_is_still_summarised() -> None:
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    truth.record_sim_swaps("SCN-0000001", [sim_swap(1, 0)])

    (instance,) = truth.finish().scenarios

    assert instance.started_at == instance.ended_at == START
    assert instance.total_amount_cents == 0


def test_normal_logins_and_sim_swaps_get_clean_labels() -> None:
    (session_label,) = normal_session_labels([session(1, 0)])
    (swap_label,) = normal_sim_swap_labels([sim_swap(1, 0)])

    assert (session_label.session_id, swap_label.sim_swap_id) == ("SES-0000001", "SIM-0000001")
    for label in (session_label, swap_label):
        assert not label.is_fraud
        assert (label.fraud_type, label.scenario_id, label.reported_at) == (None, None, None)


def test_a_sweep_between_the_victims_own_accounts_is_not_counted_as_stolen() -> None:
    def transfer_leg(number: int, account_id: str, direction: Direction) -> Transaction:
        leg = replace(
            draft(5, 0),
            account_id=account_id,
            direction=direction,
            channel=Channel.INTERNAL_TRANSFER,
            auth_method=TransactionAuthMethod.OTP,
            transfer_group_id="TRF-000000001",
            counterparty_account_id="ACC-0000009",
            session_id="SES-0000001",
            card_id=None,
            merchant_id=None,
            entry_mode=None,
        )
        return to_transaction(leg, number, APPROVED)

    payout = to_transaction(
        replace(
            draft(3, 2),
            channel=Channel.EFT,
            auth_method=TransactionAuthMethod.OTP,
            beneficiary_id="BEN-0000001",
            session_id="SES-0000001",
            card_id=None,
            merchant_id=None,
            entry_mode=None,
        ),
        3,
        APPROVED,
    )
    truth = GroundTruth()
    truth.add_episode(episode("SCN-0000001"), REPORTED_AT)
    swept = [
        transfer_leg(1, "ACC-0000002", Direction.DEBIT),
        transfer_leg(2, "ACC-0000001", Direction.CREDIT),
    ]
    truth.record("SCN-0000001", [*swept, payout])

    rows = truth.finish()

    assert rows.scenarios[0].total_amount_cents == 30_000  # only the payout left the victim
    assert len(rows.transaction_labels) == 3  # the sweep is still fraud
