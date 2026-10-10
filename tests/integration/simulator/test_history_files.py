from collections.abc import Sequence
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from sentinel.domain.base import DomainModel
from sentinel.simulator import history_files
from sentinel.simulator.arrow_schema import schema_for
from sentinel.simulator.history import MODEL_BY_TABLE, HistoryTable, simulate_history
from sentinel.simulator.history_files import HistoryWriter, history_metadata
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data

SEED = 13


class RecordingSink:
    """Passes rows on to the writer and keeps a copy of the transactions."""

    def __init__(self, writer: HistoryWriter, fail_after_days: int | None = None) -> None:
        self.writer = writer
        self.transactions: list[dict[str, object]] = []
        self._days_left = fail_after_days

    def add(self, table: HistoryTable, rows: Sequence[DomainModel]) -> None:
        self.writer.add(table, rows)
        if table is not HistoryTable.TRANSACTION:
            return
        self.transactions += [row.model_dump() for row in rows]
        if self._days_left is not None:
            self._days_left -= 1
            if self._days_left == 0:
                raise RuntimeError("disk full")


@pytest.fixture(scope="module")
def reference() -> ReferenceData:
    return generate_reference_data(SEED, customer_count=40)


def write_history(reference: ReferenceData, directory: Path) -> HistoryWriter:
    with HistoryWriter(directory, history_metadata(reference, 1)) as writer:
        simulate_history(reference, 1, writer)
    return writer


def test_writes_every_table_with_the_model_schema(reference: ReferenceData, tmp_path: Path) -> None:
    with HistoryWriter(tmp_path, history_metadata(reference, 1)) as writer:
        summary = simulate_history(reference, 1, writer)

    for table, path in writer.paths().items():
        stored = pq.read_table(path)
        assert stored.schema.remove_metadata() == schema_for(MODEL_BY_TABLE[table])
        assert stored.num_rows == summary.row_counts[table]
    assert not list(tmp_path.glob(".*.tmp"))


def test_rows_are_written_in_batches_and_round_trip_in_order(
    reference: ReferenceData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history_files, "BATCH_ROWS", 500)
    with HistoryWriter(tmp_path, history_metadata(reference, 1)) as writer:
        sink = RecordingSink(writer)
        simulate_history(reference, 1, sink)

    stored = pq.ParquetFile(writer.paths()[HistoryTable.TRANSACTION])
    assert stored.metadata.num_row_groups > 1
    assert stored.read().to_pylist() == sink.transactions


def test_files_record_the_run_that_made_them(reference: ReferenceData, tmp_path: Path) -> None:
    writer = write_history(reference, tmp_path)
    metadata = pq.read_schema(writer.paths()[HistoryTable.TRANSACTION]).metadata

    assert metadata[b"sentinel.seed"] == str(SEED).encode()
    assert metadata[b"sentinel.simulation_start"] == b"2026-01-01T00:00:00+00:00"
    assert metadata[b"sentinel.months"] == b"1"


def test_a_failed_run_keeps_the_previous_files(
    reference: ReferenceData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_history(reference, tmp_path).paths()[HistoryTable.TRANSACTION]
    rows_before = pq.read_metadata(path).num_rows

    monkeypatch.setattr(history_files, "BATCH_ROWS", 100)  # so files are already open
    with (
        pytest.raises(RuntimeError, match="disk full"),
        HistoryWriter(tmp_path, history_metadata(reference, 1)) as writer,
    ):
        simulate_history(reference, 1, RecordingSink(writer, fail_after_days=5))

    assert pq.read_metadata(path).num_rows == rows_before
    assert not list(tmp_path.glob(".*.tmp"))
