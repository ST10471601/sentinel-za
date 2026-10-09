from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.beneficiaries import Beneficiary, BeneficiaryEvent, BeneficiaryEventType


def beneficiary_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "beneficiary_id": "BEN-0000001",
        "customer_id": "CUS-0000001",
        "beneficiary_name": "Landlord",
        "payee_bank": "Imbali Bank",
        "payee_account_number": "6200123456",
        "payee_internal_account_id": None,
        "shap_id": "+27821234567",
        "created_at": datetime(2026, 1, 3, 18, 0, tzinfo=UTC),
    }
    return fields | overrides


def event_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "beneficiary_event_id": "BEV-0000001",
        "beneficiary_id": "BEN-0000001",
        "event_type": BeneficiaryEventType.CREATED,
        "event_time": datetime(2026, 1, 3, 18, 0, tzinfo=UTC),
        "session_id": "SES-0000001",
        "old_account_number": None,
        "new_account_number": "6200123456",
    }
    return fields | overrides


def test_valid_beneficiary_is_accepted() -> None:
    assert Beneficiary.model_validate(beneficiary_fields()).beneficiary_name == "Landlord"


def test_beneficiary_at_our_bank_links_the_internal_account() -> None:
    beneficiary = Beneficiary.model_validate(
        beneficiary_fields(payee_internal_account_id="ACC-0000002", shap_id=None)
    )
    assert beneficiary.payee_internal_account_id == "ACC-0000002"


@pytest.mark.parametrize(
    "overrides",
    [
        {"beneficiary_id": "BEN-001"},
        {"beneficiary_name": ""},
        {"payee_account_number": "12-34"},
        {"shap_id": "0821234567"},
    ],
)
def test_malformed_beneficiary_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Beneficiary.model_validate(beneficiary_fields(**overrides))


def test_created_event_has_only_the_new_account_number() -> None:
    assert BeneficiaryEvent.model_validate(event_fields()).old_account_number is None

    with pytest.raises(ValidationError, match="created"):
        BeneficiaryEvent.model_validate(event_fields(old_account_number="6200999999"))


def test_details_changed_event_has_old_and_new_account_numbers_that_differ() -> None:
    changed = event_fields(
        event_type=BeneficiaryEventType.DETAILS_CHANGED,
        old_account_number="6200999999",
    )
    assert BeneficiaryEvent.model_validate(changed).new_account_number == "6200123456"

    with pytest.raises(ValidationError, match="details_changed"):
        BeneficiaryEvent.model_validate(changed | {"new_account_number": "6200999999"})
    with pytest.raises(ValidationError, match="details_changed"):
        BeneficiaryEvent.model_validate(changed | {"old_account_number": None})


def test_deleted_event_has_only_the_old_account_number() -> None:
    deleted = event_fields(
        event_type=BeneficiaryEventType.DELETED,
        old_account_number="6200123456",
        new_account_number=None,
    )
    assert BeneficiaryEvent.model_validate(deleted).new_account_number is None

    with pytest.raises(ValidationError, match="deleted"):
        BeneficiaryEvent.model_validate(deleted | {"new_account_number": "6200123456"})
