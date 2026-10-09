"""Generate every reference table for one simulation, reproducibly from a seed."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel.core.datetimes import ensure_utc
from sentinel.domain.accounts import Account, Card
from sentinel.domain.base import DomainModel
from sentinel.domain.customers import Customer
from sentinel.domain.devices import CustomerDevice, Device
from sentinel.domain.merchants import Merchant
from sentinel.simulator.accounts import generate_accounts, generate_cards
from sentinel.simulator.customers import generate_customers
from sentinel.simulator.devices import generate_devices
from sentinel.simulator.merchants import generate_merchants
from sentinel.simulator.randomness import make_rng

DEFAULT_CUSTOMER_COUNT = 2_000
# A fixed start, never "now", so the same seed always gives the same data.
DEFAULT_SIMULATION_START = datetime(2026, 1, 1, tzinfo=UTC)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReferenceTable:
    """One table: its name, the model each row follows, and the rows."""

    name: str
    model: type[DomainModel]
    rows: tuple[DomainModel, ...]


@dataclass(frozen=True, slots=True)
class ReferenceData:
    """All reference tables for one simulation, and the inputs that produced them."""

    seed: int
    simulation_start: datetime
    customers: tuple[Customer, ...]
    accounts: tuple[Account, ...]
    cards: tuple[Card, ...]
    devices: tuple[Device, ...]
    customer_devices: tuple[CustomerDevice, ...]
    merchants: tuple[Merchant, ...]

    def tables(self) -> tuple[ReferenceTable, ...]:
        """Return every table in a fixed order."""
        return (
            ReferenceTable("customer", Customer, self.customers),
            ReferenceTable("account", Account, self.accounts),
            ReferenceTable("card", Card, self.cards),
            ReferenceTable("device", Device, self.devices),
            ReferenceTable("customer_device", CustomerDevice, self.customer_devices),
            ReferenceTable("merchant", Merchant, self.merchants),
        )


def generate_reference_data(
    seed: int,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    simulation_start: datetime = DEFAULT_SIMULATION_START,
) -> ReferenceData:
    """Generate customers, accounts, cards, devices and merchants.

    Everything is dated before ``simulation_start``. Each table uses its own random
    stream derived from ``seed``.
    """
    start = ensure_utc(simulation_start)
    customers = generate_customers(make_rng(seed, "customers"), customer_count, start)
    accounts = generate_accounts(make_rng(seed, "accounts"), customers, start)
    cards = generate_cards(make_rng(seed, "cards"), accounts, customers, start)
    devices, customer_devices = generate_devices(make_rng(seed, "devices"), customers, start)
    merchants = generate_merchants(make_rng(seed, "merchants"))

    reference_data = ReferenceData(
        seed=seed,
        simulation_start=start,
        customers=tuple(customers),
        accounts=tuple(accounts),
        cards=tuple(cards),
        devices=tuple(devices),
        customer_devices=tuple(customer_devices),
        merchants=tuple(merchants),
    )
    row_counts = {table.name: len(table.rows) for table in reference_data.tables()}
    logger.info("generated reference data", extra={"seed": seed, **row_counts})
    return reference_data
