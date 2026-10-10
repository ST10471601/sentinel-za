"""Stream simulated history to Parquet, one file per table, in batches to keep memory low."""

import logging
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

import pyarrow as pa
import pyarrow.parquet as pq

from sentinel.domain.base import DomainModel
from sentinel.simulator.arrow_schema import schema_for
from sentinel.simulator.history import MODEL_BY_TABLE, HistoryTable
from sentinel.simulator.reference_data import ReferenceData
from sentinel.simulator.reference_files import COMPRESSION

HISTORY_DIR_NAME = "history"
BATCH_ROWS = 50_000  # rows held in memory per table before they are written

logger = logging.getLogger(__name__)


def history_metadata(reference: ReferenceData, months: int) -> dict[str, str]:
    """Parquet metadata that ties each file to the run that produced it."""
    return {
        "sentinel.seed": str(reference.seed),
        "sentinel.simulation_start": reference.simulation_start.isoformat(),
        "sentinel.months": str(months),
    }


class HistoryWriter:
    """Writes each table to ``<directory>/<table>.parquet`` as rows arrive.

    Files are written under temporary names and renamed on a clean close, so a failed
    run never replaces the files from the last good run. Use as a context manager.
    """

    def __init__(self, directory: Path, run_metadata: dict[str, str]) -> None:
        self._directory = directory
        self._run_metadata = run_metadata
        self._buffers: dict[HistoryTable, list[dict[str, object]]] = {t: [] for t in HistoryTable}
        self._writers: dict[HistoryTable, pq.ParquetWriter] = {}

    def __enter__(self) -> Self:
        self._directory.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._finish()
        else:
            self._abandon()

    def add(self, table: HistoryTable, rows: Sequence[DomainModel]) -> None:
        """Buffer rows, writing a batch to disk once enough have built up."""
        buffer = self._buffers[table]
        buffer.extend(row.model_dump() for row in rows)
        if len(buffer) >= BATCH_ROWS:
            self._flush(table)

    def paths(self) -> dict[HistoryTable, Path]:
        """Where each table's file is written."""
        return {table: self._directory / f"{table.value}.parquet" for table in HistoryTable}

    def _flush(self, table: HistoryTable) -> None:
        buffer = self._buffers[table]
        writer = self._writers.get(table)
        if writer is None:
            writer = pq.ParquetWriter(
                self._temporary_path(table), self._schema(table), compression=COMPRESSION
            )
            self._writers[table] = writer
        writer.write_table(pa.Table.from_pylist(buffer, schema=self._schema(table)))
        buffer.clear()

    def _finish(self) -> None:
        """Write what is left, close every file and move it into place."""
        for table in HistoryTable:
            self._flush(table)  # also creates empty files, so every table always exists
            self._writers.pop(table).close()
            self._temporary_path(table).replace(self.paths()[table])
            logger.debug("wrote %s", self.paths()[table], extra={"table": table.value})

    def _abandon(self) -> None:
        for table, writer in self._writers.items():
            writer.close()
            self._temporary_path(table).unlink(missing_ok=True)
        self._writers.clear()

    def _schema(self, table: HistoryTable) -> pa.Schema:
        return schema_for(MODEL_BY_TABLE[table]).with_metadata(self._run_metadata)

    def _temporary_path(self, table: HistoryTable) -> Path:
        return self._directory / f".{table.value}.parquet.tmp"
