from collections import Counter, defaultdict

from sentinel.domain.customers import BankingChannel, CustomerType
from sentinel.domain.devices import CustomerDevice, DeviceLinkMethod, DevicePlatform
from sentinel.simulator.reference_data import DEFAULT_SIMULATION_START, ReferenceData

START = DEFAULT_SIMULATION_START


def test_every_customer_has_a_device(reference_data: ReferenceData) -> None:
    linked = {link.customer_id for link in reference_data.customer_devices}
    assert linked == {customer.customer_id for customer in reference_data.customers}


def test_links_point_to_known_devices_and_are_unique(reference_data: ReferenceData) -> None:
    device_ids = {device.device_id for device in reference_data.devices}
    pairs = [(link.customer_id, link.device_id) for link in reference_data.customer_devices]
    assert all(device_id in device_ids for _, device_id in pairs)
    assert len(pairs) == len(set(pairs))


def test_main_device_matches_preferred_channel(reference_data: ReferenceData) -> None:
    platform_by_device = {device.device_id: device.platform for device in reference_data.devices}
    first_link: dict[str, CustomerDevice] = {}
    for link in reference_data.customer_devices:
        first_link.setdefault(link.customer_id, link)

    for customer in reference_data.customers:
        platform = platform_by_device[first_link[customer.customer_id].device_id]
        if customer.preferred_channel is BankingChannel.USSD:
            assert platform is DevicePlatform.FEATURE_PHONE
        elif customer.preferred_channel is BankingChannel.INTERNET:
            assert platform is DevicePlatform.WEB
        else:
            assert platform in {DevicePlatform.ANDROID, DevicePlatform.IOS}


def test_links_are_made_after_the_device_is_first_seen(reference_data: ReferenceData) -> None:
    first_seen = {device.device_id: device.first_seen_at for device in reference_data.devices}
    for link in reference_data.customer_devices:
        assert first_seen[link.device_id] <= link.linked_at < START


def test_a_few_family_phones_are_shared_within_a_city(reference_data: ReferenceData) -> None:
    city = {customer.customer_id: customer.home_city for customer in reference_data.customers}
    individuals = {
        c.customer_id
        for c in reference_data.customers
        if c.customer_type is CustomerType.INDIVIDUAL
    }
    customers_by_device: dict[str, list[str]] = defaultdict(list)
    for link in reference_data.customer_devices:
        customers_by_device[link.device_id].append(link.customer_id)

    shared = {d: ids for d, ids in customers_by_device.items() if len(ids) > 1}
    assert 0.01 <= len(shared) / len(individuals) <= 0.03
    for customer_ids in shared.values():
        assert len({city[customer_id] for customer_id in customer_ids}) == 1
        assert set(customer_ids) <= individuals


def test_shared_phones_are_linked_by_otp(reference_data: ReferenceData) -> None:
    link_count = Counter(link.device_id for link in reference_data.customer_devices)
    seen: set[str] = set()
    for link in reference_data.customer_devices:
        if link_count[link.device_id] > 1 and link.device_id in seen:
            assert link.link_method is DeviceLinkMethod.OTP
        seen.add(link.device_id)
