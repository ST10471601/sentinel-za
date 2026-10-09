"""Write reference tables to Parquet files, one file per table."""

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from sentinel.simulator.arrow_schema import schema_for
from sentinel.simulator.reference_data import ReferenceData, ReferenceTable

REFERENCE_DIR_NAME = "reference"
COMPRESSION = "zstd"

logger = logging.getLogger(__name__)


def write_reference_data(reference_data: ReferenceData, directory: Path) -> dict[str, Path]:
    """Write every table to ``<directory>/<table>.parquet``, replacing old files.

    Each file records the seed and simulation start in its metadata, so any file can be
    traced back to the run that produced it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    run_metadata = {
        "sentinel.seed": str(reference_data.seed),
        "sentinel.simulation_start": reference_data.simulation_start.isoformat(),
    }

    paths = {}
    for table in reference_data.tables():
        path = directory / f"{table.name}.parquet"
        _write_table(table, path, run_metadata)
        paths[table.name] = path
        logger.debug("wrote %s", path, extra={"table": table.name, "rows": len(table.rows)})
    return paths


def _write_table(table: ReferenceTable, path: Path, run_metadata: dict[str, str]) -> None:
    schema = schema_for(table.model).with_metadata(run_metadata)
    rows = [row.model_dump() for row in table.rows]
    arrow_table = pa.Table.from_pylist(rows, schema=schema)

    # Write to a temporary file first, so a crash never leaves a half-written table.
    temporary_path = path.with_name(f".{path.name}.tmp")
    pq.write_table(arrow_table, temporary_path, compression=COMPRESSION)
    temporary_path.replace(path)
