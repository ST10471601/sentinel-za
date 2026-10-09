"""Generate devices and link them to customer profiles."""

import random
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from sentinel.domain.customers import BankingChannel, Customer, CustomerType, IncomeBand
from sentinel.domain.devices import CustomerDevice, Device, DeviceLinkMethod, DevicePlatform
from sentinel.simulator.identifiers import make_device_fingerprint, make_id
from sentinel.simulator.randomness import chance, random_datetime_between

SECOND_DEVICE_CHANCE = 0.25
# Share of individuals also linked to a family member's phone in the same city.
# This makes a shared device a weak fraud signal on its own, as in real data.
SHARED_DEVICE_SHARE = 0.02
MAX_DEVICE_AGE_DAYS = 3 * 365  # phones are replaced, so none is older than this

IOS_SHARE: dict[IncomeBand, float] = {
    IncomeBand.LOW: 0.05,
    IncomeBand.MIDDLE: 0.15,
    IncomeBand.HIGH: 0.40,
    IncomeBand.BUSINESS: 0.30,
}

LINK_METHOD_BY_PLATFORM: dict[DevicePlatform, DeviceLinkMethod] = {
    DevicePlatform.ANDROID: DeviceLinkMethod.APP_REGISTRATION,
    DevicePlatform.IOS: DeviceLinkMethod.APP_REGISTRATION,
    DevicePlatform.WEB: DeviceLinkMethod.OTP,
    DevicePlatform.FEATURE_PHONE: DeviceLinkMethod.BRANCH,
}

PHONE_PLATFORMS = frozenset({DevicePlatform.ANDROID, DevicePlatform.IOS})


def generate_devices(
    rng: random.Random, customers: Sequence[Customer], simulation_start: datetime
) -> tuple[list[Device], list[CustomerDevice]]:
    """Give each customer one or two devices, then add a few shared family phones."""
    oldest_device = simulation_start - timedelta(days=MAX_DEVICE_AGE_DAYS)
    devices: list[Device] = []
    links: list[CustomerDevice] = []
    main_device_by_customer: dict[str, Device] = {}

    for customer in customers:
        for platform in _pick_platforms(rng, customer):
            first_seen_at = random_datetime_between(
                rng, max(customer.onboarded_at, oldest_device), simulation_start
            )
            device = Device(
                device_id=make_id("DEV", len(devices) + 1),
                device_fingerprint=make_device_fingerprint(rng),
                platform=platform,
                first_seen_at=first_seen_at,
            )
            devices.append(device)
            main_device_by_customer.setdefault(customer.customer_id, device)
            links.append(
                CustomerDevice(
                    customer_id=customer.customer_id,
                    device_id=device.device_id,
                    linked_at=first_seen_at,
                    link_method=LINK_METHOD_BY_PLATFORM[platform],
                )
            )

    links.extend(_link_shared_phones(rng, customers, main_device_by_customer, simulation_start))
    return devices, links


def _pick_platforms(rng: random.Random, customer: Customer) -> list[DevicePlatform]:
    """Main device from the preferred channel, sometimes a second one."""
    if customer.preferred_channel is BankingChannel.USSD:
        return [DevicePlatform.FEATURE_PHONE]

    phone = (
        DevicePlatform.IOS
        if chance(rng, IOS_SHARE[customer.income_band])
        else DevicePlatform.ANDROID
    )
    main = DevicePlatform.WEB if customer.preferred_channel is BankingChannel.INTERNET else phone
    if not chance(rng, SECOND_DEVICE_CHANCE):
        return [main]
    # App users add a browser for internet banking; internet users add a phone.
    return [main, phone if main is DevicePlatform.WEB else DevicePlatform.WEB]


def _link_shared_phones(
    rng: random.Random,
    customers: Sequence[Customer],
    main_device_by_customer: dict[str, Device],
    simulation_start: datetime,
) -> list[CustomerDevice]:
    """Link a few individuals to the main phone of another individual in the same city."""
    phone_owners_by_city: dict[str, list[Customer]] = defaultdict(list)
    for customer in customers:
        device = main_device_by_customer[customer.customer_id]
        if customer.customer_type is CustomerType.INDIVIDUAL and device.platform in PHONE_PLATFORMS:
            phone_owners_by_city[customer.home_city].append(customer)

    individuals = [c for c in customers if c.customer_type is CustomerType.INDIVIDUAL]
    sharer_count = round(len(individuals) * SHARED_DEVICE_SHARE)
    links = []
    for sharer in rng.sample(individuals, sharer_count):
        owners = [c for c in phone_owners_by_city[sharer.home_city] if c is not sharer]
        if not owners:
            continue
        device = main_device_by_customer[rng.choice(owners).customer_id]
        linked_from = max(device.first_seen_at, sharer.onboarded_at)
        links.append(
            CustomerDevice(
                customer_id=sharer.customer_id,
                device_id=device.device_id,
                linked_at=random_datetime_between(rng, linked_from, simulation_start),
                link_method=DeviceLinkMethod.OTP,
            )
        )
    return links
