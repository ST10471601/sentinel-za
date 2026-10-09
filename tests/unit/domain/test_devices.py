from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.domain.devices import CustomerDevice, Device, DeviceLinkMethod, DevicePlatform

FINGERPRINT = "ab" * 32


def test_valid_device_and_link_are_accepted() -> None:
    device = Device(
        device_id="DEV-0000001",
        device_fingerprint=FINGERPRINT,
        platform=DevicePlatform.ANDROID,
        first_seen_at=datetime(2023, 1, 5, tzinfo=UTC),
    )
    link = CustomerDevice(
        customer_id="CUS-0000001",
        device_id=device.device_id,
        linked_at=device.first_seen_at,
        link_method=DeviceLinkMethod.APP_REGISTRATION,
    )
    assert link.device_id == "DEV-0000001"


@pytest.mark.parametrize("fingerprint", ["AB" * 32, "ab" * 31, "zz" * 32])
def test_fingerprint_must_be_64_lowercase_hex_characters(fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        Device(
            device_id="DEV-0000001",
            device_fingerprint=fingerprint,
            platform=DevicePlatform.IOS,
            first_seen_at=datetime(2023, 1, 5, tzinfo=UTC),
        )
