"""Command-line interface: `uv run sentinel <command>`.

The only place where concrete implementations are created and wired together.
"""

from typing import Annotated

import typer

from sentinel import __version__
from sentinel.core.config import Settings, get_settings
from sentinel.core.errors import ConfigurationError, SimulationError
from sentinel.core.logs import configure_logging
from sentinel.simulator.reference_data import DEFAULT_CUSTOMER_COUNT, generate_reference_data
from sentinel.simulator.reference_files import REFERENCE_DIR_NAME, write_reference_data

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


@simulate_app.command("reference")
def simulate_reference(
    customers: Annotated[
        int, typer.Option(min=1, help="Number of customers to generate.")
    ] = DEFAULT_CUSTOMER_COUNT,
    seed: Annotated[
        int | None, typer.Option(min=0, help="Random seed. Defaults to SENTINEL_SEED.")
    ] = None,
) -> None:
    """Generate customers, accounts, cards, devices and merchants as Parquet files."""
    settings = load_settings()
    try:
        reference_data = generate_reference_data(
            seed if seed is not None else settings.seed, customers
        )
    except SimulationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=SIMULATION_ERROR_EXIT_CODE) from exc

    paths = write_reference_data(reference_data, settings.data_dir / REFERENCE_DIR_NAME)
    for table in reference_data.tables():
        typer.echo(f"{table.name:<16}{len(table.rows):>8,} rows  {paths[table.name]}")
