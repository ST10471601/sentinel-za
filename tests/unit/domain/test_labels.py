from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sentinel.domain.labels import AccountLabel, FraudType, ScenarioInstance, TransactionLabel

STARTED_AT = datetime(2026, 2, 3, 22, 40, tzinfo=UTC)


def scenario_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "scenario_id": "SCN-0000001",
        "fraud_type": FraudType.CARD_NOT_PRESENT,
        "victim_customer_id": "CUS-0000001",
        "started_at": STARTED_AT,
        "ended_at": STARTED_AT + timedelta(hours=2),
        "total_amount_cents": 1_250_000,
        "params": '{"test_charges": 2, "foreign": true}',
    }
    return fields | overrides


def label_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "transaction_id": "TXN-000000001",
        "is_fraud": True,
        "fraud_type": FraudType.CARD_NOT_PRESENT,
        "scenario_id": "SCN-0000001",
        "reported_at": STARTED_AT + timedelta(days=4),
    }
    return fields | overrides


def test_valid_scenario_is_accepted() -> None:
    scenario = ScenarioInstance.model_validate(scenario_fields())
    assert scenario.fraud_type is FraudType.CARD_NOT_PRESENT


def test_scenario_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="ended_at"):
        ScenarioInstance.model_validate(scenario_fields(ended_at=STARTED_AT - timedelta(seconds=1)))


def test_fully_declined_scenario_has_zero_total() -> None:
    scenario = ScenarioInstance.model_validate(scenario_fields(total_amount_cents=0))
    assert scenario.total_amount_cents == 0


def test_mule_layering_has_no_victim() -> None:
    layering = scenario_fields(fraud_type=FraudType.MULE_LAYERING, victim_customer_id=None)
    assert ScenarioInstance.model_validate(layering).victim_customer_id is None

    with pytest.raises(ValidationError, match="no victim"):
        ScenarioInstance.model_validate(layering | {"victim_customer_id": "CUS-0000001"})


def test_other_fraud_types_need_a_victim() -> None:
    with pytest.raises(ValidationError, match="needs a victim"):
        ScenarioInstance.model_validate(scenario_fields(victim_customer_id=None))


@pytest.mark.parametrize(("params", "message"), [("not json", "valid JSON"), ("[1, 2]", "object")])
def test_params_must_be_a_json_object(params: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ScenarioInstance.model_validate(scenario_fields(params=params))


def test_fraud_label_is_accepted_with_or_without_a_report() -> None:
    assert TransactionLabel.model_validate(label_fields()).is_fraud
    unreported = TransactionLabel.model_validate(label_fields(reported_at=None))
    assert unreported.reported_at is None


@pytest.mark.parametrize("missing", ["fraud_type", "scenario_id"])
def test_fraud_label_needs_type_and_scenario(missing: str) -> None:
    with pytest.raises(ValidationError, match="need fraud_type and scenario_id"):
        TransactionLabel.model_validate(label_fields(**{missing: None}))


def test_normal_label_has_no_fraud_fields() -> None:
    normal = {"is_fraud": False, "fraud_type": None, "scenario_id": None, "reported_at": None}
    assert not TransactionLabel.model_validate(label_fields(**normal)).is_fraud

    for field, value in [("fraud_type", FraudType.APP_VISHING), ("reported_at", STARTED_AT)]:
        with pytest.raises(ValidationError, match="normal transactions"):
            TransactionLabel.model_validate(label_fields(**(normal | {field: value})))


def test_mule_since_is_set_only_for_mules() -> None:
    mule = AccountLabel(account_id="ACC-0000001", is_mule=True, mule_since=STARTED_AT)
    normal = AccountLabel(account_id="ACC-0000002", is_mule=False, mule_since=None)
    assert mule.is_mule
    assert not normal.is_mule

    with pytest.raises(ValidationError, match="mule_since"):
        AccountLabel(account_id="ACC-0000001", is_mule=True, mule_since=None)
    with pytest.raises(ValidationError, match="mule_since"):
        AccountLabel(account_id="ACC-0000002", is_mule=False, mule_since=STARTED_AT)
