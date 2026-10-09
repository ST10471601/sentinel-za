"""Login sessions: a customer opening the app, internet banking or USSD."""

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sentinel.domain.customers import BankingChannel, Customer
from sentinel.domain.devices import CustomerDevice, Device, DevicePlatform
from sentinel.domain.sessions import LoginSession, SessionAuthMethod
from sentinel.simulator.geography import jitter_point
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.randomness import chance

HOME_COUNTRY = "ZA"
APP_LOCATION_JITTER_DEGREES = 0.05  # about 5 km around home
BIOMETRIC_LOGIN_SHARE = 0.7  # of app logins; the rest type a password

CHANNEL_BY_PLATFORM: dict[DevicePlatform, BankingChannel] = {
    DevicePlatform.ANDROID: BankingChannel.APP,
    DevicePlatform.IOS: BankingChannel.APP,
    DevicePlatform.WEB: BankingChannel.INTERNET,
    DevicePlatform.FEATURE_PHONE: BankingChannel.USSD,
}


@dataclass(frozen=True, slots=True)
class LinkedDevice:
    """A device on a customer's profile, usable from the moment it was linked."""

    device: Device
    linked_at: datetime


def group_devices_by_customer(
    devices: Sequence[Device], customer_devices: Sequence[CustomerDevice]
) -> dict[str, list[LinkedDevice]]:
    """Map each customer ID to the devices linked to their profile."""
    device_by_id = {device.device_id: device for device in devices}
    grouped: dict[str, list[LinkedDevice]] = defaultdict(list)
    for link in customer_devices:
        grouped[link.customer_id].append(LinkedDevice(device_by_id[link.device_id], link.linked_at))
    return grouped


def devices_linked_by(linked: Sequence[LinkedDevice], moment: datetime) -> list[Device]:
    """The customer's devices that were already linked at ``moment``."""
    return [item.device for item in linked if item.linked_at <= moment]


def start_session(
    rng: random.Random,
    session_ids: IdSequence,
    customer: Customer,
    devices: Sequence[Device],
    started_at: datetime,
) -> LoginSession:
    """Log the customer in on one of the given devices, which must be linked to them."""
    device = rng.choice(devices)
    channel = CHANNEL_BY_PLATFORM[device.platform]

    # USSD runs over the mobile network: no app, no IP address, no location.
    if channel is BankingChannel.USSD:
        return LoginSession(
            session_id=session_ids.next_id(),
            customer_id=customer.customer_id,
            device_id=None,
            channel=channel,
            started_at=started_at,
            auth_method=SessionAuthMethod.PIN,
            ip_country=None,
            lat=None,
            lon=None,
            remote_access_detected=False,
        )

    # Only the app reports a location; a browser session has none.
    location: tuple[float | None, float | None] = (None, None)
    auth_method = SessionAuthMethod.PASSWORD
    if channel is BankingChannel.APP:
        location = jitter_point(
            rng, customer.home_lat, customer.home_lon, APP_LOCATION_JITTER_DEGREES
        )
        if chance(rng, BIOMETRIC_LOGIN_SHARE):
            auth_method = SessionAuthMethod.BIOMETRIC

    return LoginSession(
        session_id=session_ids.next_id(),
        customer_id=customer.customer_id,
        device_id=device.device_id,
        channel=channel,
        started_at=started_at,
        auth_method=auth_method,
        ip_country=HOME_COUNTRY,
        lat=location[0],
        lon=location[1],
        remote_access_detected=False,
    )
