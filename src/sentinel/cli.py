"""Command-line entry point: `uv run sentinel <command>`.

Commands are added phase by phase (simulate, ingest, score, ui, demo).
"""

import typer

from sentinel import __version__

app = typer.Typer(
    help="Sentinel ZA — real-time bank fraud detection platform.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Show the installed version."""
    typer.echo(f"sentinel-za {__version__}")


@app.callback()
def main() -> None:
    """Sentinel ZA command-line interface."""


if __name__ == "__main__":
    app()
