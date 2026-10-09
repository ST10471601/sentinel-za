import pytest

from sentinel.simulator.reference_data import ReferenceData, generate_reference_data

SEED = 7


@pytest.fixture(scope="session")
def reference_data() -> ReferenceData:
    """One default-sized simulation shared by the read-only tests."""
    return generate_reference_data(SEED)
