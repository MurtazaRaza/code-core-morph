"""``codecoremorph toolchain install|list|path`` (FR-1.13, SPEC §7.15)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stdout
from codecoremorph.core.paths import HomeLayout
from codecoremorph.sandbox.toolchains import (
    LANGUAGES,
    install_command,
    installed_tools,
    run_install,
)

toolchain_app = typer.Typer(
    help="Native toolchains (used when Docker isn't).", no_args_is_help=True
)


@toolchain_app.command("install")
@handle_errors
def toolchain_install(
    language: Annotated[str, typer.Argument(help="cpp, or all.")],
    force: Annotated[bool, typer.Option("--force", help="Reinstall pinned tools.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be downloaded; change nothing.")
    ] = False,
    prefix: Annotated[
        Path | None, typer.Option("--prefix", help="Install here instead of CODECOREMORPH_HOME.")
    ] = None,
) -> None:
    """Download, verify and install a pinned native toolchain (no admin rights needed)."""
    command = install_command(language, force=force, dry_run=dry_run, prefix=prefix)
    code = run_install(command)
    if code != 0:
        raise typer.Exit(code)


@toolchain_app.command("list")
@handle_errors
def toolchain_list() -> None:
    """Show the pinned tools for this machine and whether each is installed."""
    home = HomeLayout.resolve()
    table = Table(box=None, pad_edge=False, show_header=False)
    for language in LANGUAGES:
        tools = installed_tools(home, language)
        env = home.toolchain_env(language)
        for tool in tools:
            table.add_row(
                status_label("ok" if tool.installed else "warn"),
                f"{language}: {tool.tool} {tool.version}",
                Text(str(tool.directory) if tool.installed else "not installed", style="dim"),
            )
        table.add_row(
            status_label("ok" if env.is_file() else "warn"),
            f"{language}: environment",
            Text(str(env) if env.is_file() else "not written yet", style="dim"),
        )
        if not all(t.installed for t in tools) or not env.is_file():
            table.add_row("", "", Text(f"-> codecoremorph toolchain install {language}"))
    stdout().print(table)


@toolchain_app.command("path")
@handle_errors
def toolchain_path(
    language: Annotated[
        str | None, typer.Argument(help="Print this language's environment file instead.")
    ] = None,
) -> None:
    """Print where native toolchains are installed."""
    home = HomeLayout.resolve()
    typer.echo(str(home.toolchain_env(language) if language else home.toolchains))
