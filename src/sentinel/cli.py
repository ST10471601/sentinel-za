"""Command-line interface: `uv run sentinel <command>`.

The only place where concrete implementations are created and wired together.
"""

from pathlib import Path
from typing import Annotated

import typer

from sentinel import __version__
from sentinel.core.config import Settings, get_settings
from sentinel.core.errors import ConfigurationError, SimulationError
from sentinel.core.logs import configure_logging
from sentinel.simulator.history import simulate_history
from sentinel.simulator.history_files import HistoryWriter, history_metadata
from sentinel.simulator.reference_data import (
    DEFAULT_CUSTOMER_COUNT,
    ReferenceData,
    generate_reference_data,
)
from sentinel.simulator.reference_files import REFERENCE_DIR_NAME, write_reference_data

DEFAULT_MONTHS = 3
SIMULATION_ERROR_EXIT_CODE = 1
CONFIG_ERROR_EXIT_CODE = 2

app = typer.Typer(help="Sentinel ZA: real-time bank fraud detection.", no_args_is_help=True)
simulate_app = typer.Typer(help="Generate synthetic bank data.", no_args_is_help=True)
app.add_typer(simulate_app, name="simulate")


def load_settings() -> Settings:
    """Load settings, or exit with a readable error instead of a traceback."""
    try:
        return get_settings()
    except ConfigurationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from exc


@app.callback()
def main() -> None:
    """Sentinel ZA command-line interface."""
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Show the installed version."""
    typer.echo(f"sentinel-za {__version__}")


@app.command("config")
def show_config() -> None:
    """Show the settings in use, after .env and environment overrides."""
    for name, value in load_settings().model_dump(mode="json").items():
        typer.echo(f"{name} = {value}")


CustomersOption = Annotated[int, typer.Option(min=1, help="Number of customers to generate.")]
SeedOption = Annotated[
    int | None, typer.Option(min=0, help="Random seed. Defaults to SENTINEL_SEED.")
]


@simulate_app.command("reference")
def simulate_reference(
    customers: CustomersOption = DEFAULT_CUSTOMER_COUNT, seed: SeedOption = None
) -> None:
    """Generate customers, accounts, cards, devices and merchants as Parquet files."""
    settings = load_settings()
    reference_data = _generate_reference(seed if seed is not None else settings.seed, customers)
    _write_reference(reference_data, settings.data_dir)


@simulate_app.command("history")
def simulate_history_command(
    customers: CustomersOption = DEFAULT_CUSTOMER_COUNT,
    months: Annotated[int, typer.Option(min=1, help="Months of activity to simulate.")] = (
        DEFAULT_MONTHS
    ),
    seed: SeedOption = None,
) -> None:
    """Generate reference data, then months of activity with fraud, as Parquet files."""
    settings = load_settings()
    reference_data = _generate_reference(seed if seed is not None else settings.seed, customers)
    _write_reference(reference_data, settings.data_dir)

    writer = HistoryWriter(settings.data_dir, history_metadata(reference_data, months))
    try:
        with writer:
            summary = simulate_history(reference_data, months, writer)
    except SimulationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=SIMULATION_ERROR_EXIT_CODE) from exc

    typer.echo(f"history {summary.first_day} to {summary.last_day}")
    paths = writer.paths()
    for table, count in summary.row_counts.items():
        typer.echo(f"{table.value:<18}{count:>10,} rows  {paths[table]}")
    typer.echo(f"declined transactions: {summary.declined_count:,}")
    typer.echo(f"fraud transactions: {summary.fraud_count:,}")


def _generate_reference(seed: int, customers: int) -> ReferenceData:
    try:
        return generate_reference_data(seed, customers)
    except SimulationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=SIMULATION_ERROR_EXIT_CODE) from exc


def _write_reference(reference_data: ReferenceData, data_dir: Path) -> None:
    paths = write_reference_data(reference_data, data_dir / REFERENCE_DIR_NAME)
    for table in reference_data.tables():
        typer.echo(f"{table.name:<18}{len(table.rows):>10,} rows  {paths[table.name]}")
