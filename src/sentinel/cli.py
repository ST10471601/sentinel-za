"""Command-line interface: `uv run sentinel <command>`.

The only place where concrete implementations are created and wired together.
"""

import typer

from sentinel import __version__
from sentinel.core.config import Settings, get_settings
from sentinel.core.errors import ConfigurationError
from sentinel.core.logs import configure_logging

CONFIG_ERROR_EXIT_CODE = 2

app = typer.Typer(help="Sentinel ZA: real-time bank fraud detection.", no_args_is_help=True)


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
