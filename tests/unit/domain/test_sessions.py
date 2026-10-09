from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.customers import BankingChannel
from sentinel.domain.sessions import LoginSession, SessionAuthMethod


def session_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "session_id": "SES-0000001",
        "customer_id": "CUS-0000001",
        "device_id": "DEV-0000001",
        "channel": BankingChannel.APP,
        "started_at": datetime(2026, 1, 5, 7, 30, tzinfo=UTC),
        "auth_method": SessionAuthMethod.BIOMETRIC,
        "ip_country": "ZA",
        "lat": -26.2041,
        "lon": 28.0473,
        "remote_access_detected": False,
    }
    return fields | overrides


def test_valid_app_session_is_accepted() -> None:
    session = LoginSession.model_validate(session_fields())
    assert session.channel is BankingChannel.APP


def test_ussd_session_has_no_device_or_ip_country() -> None:
    ussd = session_fields(
        channel=BankingChannel.USSD,
        auth_method=SessionAuthMethod.PIN,
        device_id=None,
        ip_country=None,
    )
    assert LoginSession.model_validate(ussd).device_id is None

    with pytest.raises(ValidationError, match="ussd"):
        LoginSession.model_validate(ussd | {"device_id": "DEV-0000001"})


@pytest.mark.parametrize("missing", ["device_id", "ip_country"])
def test_app_and_internet_sessions_need_device_and_ip_country(missing: str) -> None:
    with pytest.raises(ValidationError, match="device_id and ip_country"):
        LoginSession.model_validate(session_fields(**{missing: None}))


@pytest.mark.parametrize("overrides", [{"lat": None}, {"lon": None}])
def test_location_needs_both_coordinates(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="lat and lon"):
        LoginSession.model_validate(session_fields(**overrides))


def test_session_without_location_is_accepted() -> None:
    session = LoginSession.model_validate(session_fields(lat=None, lon=None))
    assert session.lat is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_id": "SES-1"},
        {"ip_country": "ZAF"},
        {"auth_method": "fingerprint"},
        {"started_at": datetime(2026, 1, 5, 7, 30)},  # noqa: DTZ001 - deliberately naive
    ],
)
def test_malformed_session_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LoginSession.model_validate(session_fields(**overrides))
