import random
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.domain.customers import BankingChannel, Customer
from sentinel.domain.devices import Device, DevicePlatform
from sentinel.domain.sessions import SessionAuthMethod
from sentinel.simulator.identifiers import IdSequence
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData
from sentinel.simulator.sessions import (
    LinkedDevice,
    devices_linked_by,
    group_devices_by_customer,
    start_session,
)

FIRST_SEEN = datetime(2025, 6, 1, tzinfo=UTC)


def device(platform: DevicePlatform) -> Device:
    return Device(
        device_id="DEV-0000009",
        device_fingerprint="ab" * 32,
        platform=platform,
        first_seen_at=FIRST_SEEN,
    )


@pytest.fixture(scope="module")
def customer(reference_data: ReferenceData) -> Customer:
    return reference_data.customers[0]


def test_every_customer_has_at_least_one_linked_device(reference_data: ReferenceData) -> None:
    grouped = group_devices_by_customer(reference_data.devices, reference_data.customer_devices)
    assert set(grouped) == {customer.customer_id for customer in reference_data.customers}
    assert all(grouped.values())


def test_only_devices_linked_by_the_moment_are_usable() -> None:
    phone = device(DevicePlatform.ANDROID)
    linked = [LinkedDevice(phone, FIRST_SEEN)]
    assert devices_linked_by(linked, FIRST_SEEN - timedelta(seconds=1)) == []
    assert devices_linked_by(linked, FIRST_SEEN) == [phone]


@pytest.mark.parametrize("platform", [DevicePlatform.ANDROID, DevicePlatform.IOS])
def test_app_session_reports_a_location_near_home(
    customer: Customer, platform: DevicePlatform
) -> None:
    session = start_session(
        random.Random(1), IdSequence("SES"), customer, [device(platform)], FIRST_SEEN
    )
    assert session.channel is BankingChannel.APP
    assert session.device_id == "DEV-0000009"
    assert session.ip_country == "ZA"
    assert session.lat is not None and abs(session.lat - customer.home_lat) <= 0.05
    assert session.auth_method in {SessionAuthMethod.BIOMETRIC, SessionAuthMethod.PASSWORD}


def test_internet_session_has_no_location_and_uses_a_password(customer: Customer) -> None:
    session = start_session(
        random.Random(1), IdSequence("SES"), customer, [device(DevicePlatform.WEB)], FIRST_SEEN
    )
    assert session.channel is BankingChannel.INTERNET
    assert session.lat is None
    assert session.auth_method is SessionAuthMethod.PASSWORD


def test_ussd_session_has_no_device_ip_or_location(customer: Customer) -> None:
    phone = device(DevicePlatform.FEATURE_PHONE)
    session = start_session(random.Random(1), IdSequence("SES"), customer, [phone], FIRST_SEEN)
    assert session.channel is BankingChannel.USSD
    assert (session.device_id, session.ip_country, session.lat) == (None, None, None)
    assert session.auth_method is SessionAuthMethod.PIN


def test_sessions_take_consecutive_ids(customer: Customer) -> None:
    session_ids = IdSequence("SES")
    phone = [device(DevicePlatform.ANDROID)]
    first = start_session(random.Random(1), session_ids, customer, phone, FIRST_SEEN)
    second = start_session(random.Random(1), session_ids, customer, phone, FIRST_SEEN)
    assert (first.session_id, second.session_id) == ("SES-0000001", "SES-0000002")


def test_every_customer_can_log_in_on_their_own_devices(reference_data: ReferenceData) -> None:
    grouped = group_devices_by_customer(reference_data.devices, reference_data.customer_devices)
    session_ids = IdSequence("SES")
    generator = random.Random(5)
    for customer in reference_data.customers:
        devices = devices_linked_by(grouped[customer.customer_id], DEFAULT_SIMULATION_START)
        session = start_session(generator, session_ids, customer, devices, DEFAULT_SIMULATION_START)
        assert session.device_id is None or session.device_id in {d.device_id for d in devices}
