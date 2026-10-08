"""Command-line interface and composition root: ``uv run sentinel <command>``.

This is the only module that wires concrete implementations together. Every other
package receives its dependencies as arguments, which keeps them easy to test and
to change in isolation. Commands are added phase by phase (simulate, ingest, score,
ui, demo).
"""

import typer

from sentinel import __version__
from sentinel.core.config import Settings, get_settings
from sentinel.core.errors import SentinelError
from sentinel.core.logs import configure_logging

EXIT_CONFIGURATION_ERROR = 2

app = typer.Typer(
    help="Sentinel ZA: real-time bank fraud detection platform.",
    no_args_is_help=True,
)


def _load_settings() -> Settings:
    """Load settings, turning configuration errors into a clean CLI exit."""
    try:
        return get_settings()
    except SentinelError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIGURATION_ERROR) from exc


@app.callback()
def main() -> None:
    """Sentinel ZA command-line interface."""
    settings = _load_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Show the installed version."""
    typer.echo(f"sentinel-za {__version__}")


@app.command("config")
def show_config() -> None:
    """Show the effective settings after environment and .env overrides."""
    for name, value in _load_settings().model_dump(mode="json").items():
        typer.echo(f"{name} = {value}")
