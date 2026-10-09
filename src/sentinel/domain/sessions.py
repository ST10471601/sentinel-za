"""Digital banking sessions: app, internet banking and USSD logins."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.base import CountryCode, DomainModel, Latitude, Longitude
from sentinel.domain.customers import BankingChannel, CustomerId
from sentinel.domain.devices import DeviceId

SessionId = Annotated[str, Field(pattern=r"^SES-\d{7}$")]


class SessionAuthMethod(StrEnum):
    """How the customer proved who they are when logging in."""

    PASSWORD = "password"
    BIOMETRIC = "biometric"
    OTP = "otp"
    PIN = "pin"


class LoginSession(DomainModel):
    """One login to a digital banking channel.

    USSD runs over the mobile network, so it has no registered device or IP address.
    """

    session_id: SessionId
    customer_id: CustomerId
    device_id: DeviceId | None
    channel: BankingChannel
    started_at: UtcDatetime
    auth_method: SessionAuthMethod
    ip_country: CountryCode | None
    lat: Latitude | None
    lon: Longitude | None
    remote_access_detected: bool

    @model_validator(mode="after")
    def check_channel_fields(self) -> Self:
        """USSD has no device or IP country; app and internet sessions need both."""
        has_device_and_ip = self.device_id is not None and self.ip_country is not None
        has_either = self.device_id is not None or self.ip_country is not None
        if self.channel is BankingChannel.USSD and has_either:
            raise ValueError("ussd sessions have no device_id or ip_country")
        if self.channel is not BankingChannel.USSD and not has_device_and_ip:
            raise ValueError("app and internet sessions need device_id and ip_country")
        return self

    @model_validator(mode="after")
    def check_location(self) -> Self:
        """Coordinates are given together or not at all."""
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must both be set or both be empty")
        return self
