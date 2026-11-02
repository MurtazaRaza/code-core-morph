"""``codecoremorph validate`` and ``codecoremorph schema``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stdout
from codecoremorph.config import (
    DEFAULT_CONFIG_NAME,
    SCHEMA_VERSION,
    ConfigValidationError,
    IssueSeverity,
    load_config,
    manifest_json_schema,
)
from codecoremorph.orchestrator.session import migration_issues


@handle_errors
def validate_command(
    config: Annotated[
        Path,
        typer.Argument(help="Migration manifest to check.", show_default=True),
    ] = Path(DEFAULT_CONFIG_NAME),
) -> None:
    """Validate a migration manifest and list every problem with its location.

    Beyond the schema, checks the standards and rules against the installed language adapter
    and rule packs: known standards, no downgrade, and every rule applicable to the migration.
    """
    loaded = load_config(config)
    issues = sorted(
        [*loaded.warnings, *migration_issues(config, loaded)],
        key=lambda i: (i.line or 0, i.column or 0, i.path),
    )
    if any(i.severity is IssueSeverity.ERROR for i in issues):
        raise ConfigValidationError(loaded.source, issues)
    console = stdout()
    for warning in issues:
        console.print(Text(warning.render(loaded.source), style="yellow"))
    cfg = loaded.config
    rules = ", ".join(cfg.migration.target.rules) or "none"
    console.print(
        Text.assemble(
            status_label("ok"),
            f" {loaded.source} is a valid manifest (version {SCHEMA_VERSION})",
        )
    )
    source, target = cfg.migration.source, cfg.migration.target
    console.print(
        Text(
            f"     {cfg.project.name}: {source.language} {source.standard} -> "
            f"{target.language} {target.standard}; rules: {rules}"
        )
    )


@handle_errors
def schema_command(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the schema to this file instead of stdout."),
    ] = None,
) -> None:
    """Print the manifest's JSON Schema, for editor completion and validation."""
    text = json.dumps(manifest_json_schema(), indent=2, ensure_ascii=False) + "\n"
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    stdout().print(Text.assemble(status_label("ok"), f" wrote {output}"))
