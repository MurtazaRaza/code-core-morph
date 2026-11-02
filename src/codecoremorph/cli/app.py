"""The ``codecoremorph`` command-line application."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Annotated

import typer

from codecoremorph import __version__
from codecoremorph.cli.debug import debug_app
from codecoremorph.cli.doctor import doctor_command
from codecoremorph.cli.output import print_error, stderr
from codecoremorph.cli.plan import plan_command
from codecoremorph.cli.plugins import adapters_app, rules_app
from codecoremorph.cli.toolchain import toolchain_app
from codecoremorph.cli.validate import schema_command, validate_command
from codecoremorph.core.errors import CodeCoreMorphError, ExitCode
from codecoremorph.logging import configure_logging

app = typer.Typer(
    name="codecoremorph",
    help="Migrate codebases with LLM agents in a compile, test and fix loop.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"codecoremorph {__version__}")
        raise typer.Exit()


@app.callback()
def _global_options(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logging.")] = False,
    log_file: Annotated[
        Path | None,
        typer.Option("--log-file", help="Also write a JSON-lines debug log to this file."),
    ] = None,
) -> None:
    configure_logging(level=logging.DEBUG if verbose else logging.INFO, json_log_path=log_file)


app.command("validate")(validate_command)
app.command("schema")(schema_command)
app.command("doctor")(doctor_command)
app.command("plan")(plan_command)
app.add_typer(adapters_app, name="adapters")
app.add_typer(rules_app, name="rules")
app.add_typer(toolchain_app, name="toolchain")
app.add_typer(debug_app, name="debug")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    try:
        app(args=argv, prog_name="codecoremorph")
    except CodeCoreMorphError as exc:  # raised outside a command body
        print_error(exc)
        raise SystemExit(int(exc.exit_code)) from exc
    except KeyboardInterrupt:
        stderr().print("aborted", markup=False)
        raise SystemExit(int(ExitCode.ABORTED)) from None
    except Exception as exc:  # last-resort handler: report the bug, exit 10
        logging.getLogger(__name__).debug("internal error", exc_info=exc)
        console = stderr()
        console.print(traceback.format_exc(), markup=False, highlight=False)
        console.print(
            f"internal error: {type(exc).__name__}: {exc}\n"
            "  This is a bug in CodeCoreMorph. Please report it with the traceback above.",
            markup=False,
        )
        raise SystemExit(int(ExitCode.INTERNAL)) from exc
