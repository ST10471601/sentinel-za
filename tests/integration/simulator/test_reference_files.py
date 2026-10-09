from pathlib import Path

import pyarrow.parquet as pq
import pytest

from sentinel.simulator.arrow_schema import schema_for
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data
from sentinel.simulator.reference_files import write_reference_data

SEED = 11


@pytest.fixture(scope="module")
def reference_data() -> ReferenceData:
    return generate_reference_data(SEED, customer_count=100)


def test_writes_one_file_per_table(reference_data: ReferenceData, tmp_path: Path) -> None:
    paths = write_reference_data(reference_data, tmp_path / "reference")

    assert set(paths) == {table.name for table in reference_data.tables()}
    assert sorted(path.name for path in (tmp_path / "reference").iterdir()) == sorted(
        f"{name}.parquet" for name in paths
    )


def test_files_round_trip_exactly(reference_data: ReferenceData, tmp_path: Path) -> None:
    paths = write_reference_data(reference_data, tmp_path)

    for table in reference_data.tables():
        stored = pq.read_table(paths[table.name])
        assert stored.schema.remove_metadata() == schema_for(table.model)
        assert stored.to_pylist() == [row.model_dump() for row in table.rows], table.name


def test_files_record_the_run_that_made_them(reference_data: ReferenceData, tmp_path: Path) -> None:
    paths = write_reference_data(reference_data, tmp_path)
    metadata = pq.read_schema(paths["merchant"]).metadata

    assert metadata[b"sentinel.seed"] == str(SEED).encode()
    assert metadata[b"sentinel.simulation_start"] == b"2026-01-01T00:00:00+00:00"


def test_rewriting_replaces_files_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    write_reference_data(generate_reference_data(1, customer_count=50), tmp_path)
    paths = write_reference_data(generate_reference_data(2, customer_count=20), tmp_path)

    assert pq.read_metadata(paths["customer"]).num_rows == 20
    assert not list(tmp_path.glob(".*.tmp"))
