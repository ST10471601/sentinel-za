"""Devices customers bank on, and which customer profiles they are linked to."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.base import DomainModel
from sentinel.domain.customers import CustomerId

DeviceId = Annotated[str, Field(pattern=r"^DEV-\d{7}$")]
DeviceFingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DevicePlatform(StrEnum):
    """Operating system or client type."""

    ANDROID = "android"
    IOS = "ios"
    WEB = "web"
    FEATURE_PHONE = "feature_phone"


class DeviceLinkMethod(StrEnum):
    """How a device was registered to a customer profile."""

    APP_REGISTRATION = "app_registration"
    OTP = "otp"
    BRANCH = "branch"


class Device(DomainModel):
    """One physical device or browser.

    A device can be linked to several customers. Legitimately that is a shared family
    phone; one device linked to many unrelated victims is a strong fraud signal.
    """

    device_id: DeviceId
    device_fingerprint: DeviceFingerprint
    platform: DevicePlatform
    first_seen_at: UtcDatetime


class CustomerDevice(DomainModel):
    """Link between a customer and a device registered to their profile."""

    customer_id: CustomerId
    device_id: DeviceId
    linked_at: UtcDatetime
    link_method: DeviceLinkMethod
