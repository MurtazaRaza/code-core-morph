"""``codecoremorph adapters list`` and ``codecoremorph rules list`` (FR-9.4, FR-15.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.cli.output import handle_errors, stderr, stdout
from codecoremorph.config import load_config
from codecoremorph.rules.registry import RuleRegistry

adapters_app = typer.Typer(help="Language adapters.", no_args_is_help=True)
rules_app = typer.Typer(help="Migration rules.", no_args_is_help=True)
providers_app = typer.Typer(help="LLM providers.", no_args_is_help=True)


def _problems(problems: list[str]) -> None:
    console = stderr()
    for problem in problems:
        console.print(Text(f"warning: {problem}", style="yellow"))


@adapters_app.command("list")
@handle_errors
def adapters_list() -> None:
    """List the installed language adapters."""
    registry = AdapterRegistry.discover()
    table = Table(box=None, pad_edge=False)
    for column in ("language", "adapter", "api", "from"):
        table.add_column(column, no_wrap=True)
    table.add_column("optional methods")
    for language, info in sorted(registry.adapters.items()):
        adapter = info.adapter()
        caps = adapter.capabilities()
        optional = [
            name
            for name in (
                "parse_check",
                "format",
                "register_test_files",
                "extract_assertions",
                "detect_disabled_tests",
                "scaffold_project",
            )
            if getattr(caps, name)
        ]
        table.add_row(
            language,
            info.adapter.__qualname__,
            info.adapter.api_version,
            info.distribution or "-",
            Text(", ".join(optional) or "-"),
        )
    stdout().print(table)
    _problems(registry.problems)


@rules_app.command("list")
@handle_errors
def rules_list(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Also load this manifest's migration.rules_paths."),
    ] = None,
    language: Annotated[
        str | None, typer.Option("--language", "-l", help="Only rules for this language.")
    ] = None,
) -> None:
    """List the available rules and where they come from."""
    rules_paths: list[Path] = []
    if config is not None:
        cfg = load_config(config).config
        root = (config.parent / cfg.project.root).resolve()
        rules_paths = [root / p for p in cfg.migration.rules_paths]
    registry = RuleRegistry.discover(rules_paths)
    console = stdout()
    for rule_id, loaded in sorted(registry.rules.items()):
        rule = loaded.rule
        if language is not None and rule.language != language:
            continue
        parts = [
            name
            for name, present in (
                ("detector", rule.detector is not None),
                ("codemods", bool(rule.codemods)),
                ("llm", rule.llm is not None),
            )
            if present
        ]
        console.print(
            Text.assemble(
                (rule_id, "bold"),
                f"  {rule.language}, {rule.kind.value}, {' + '.join(parts)} (pack {loaded.pack})",
            )
        )
    _problems(registry.problems)


@providers_app.command("list")
@handle_errors
def providers_list(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Also show which model serves each role."),
    ] = None,
) -> None:
    """List the installed LLM providers, and the model of each role in a manifest."""
    from codecoremorph.core.models import Role
    from codecoremorph.llm.base import ProviderRegistry
    from codecoremorph.llm.routing import Router

    registry = ProviderRegistry.discover()
    table = Table(box=None, pad_edge=False)
    for column in ("provider", "runs", "api", "from"):
        table.add_column(column, no_wrap=True)
    for name, info in sorted(registry.providers.items()):
        local = "locally" if info.plugin.local else "as a service"
        table.add_row(name, local, info.plugin.api_version, info.distribution or "-")
    console = stdout()
    console.print(table)
    if config is not None:
        llm = load_config(config).config.llm
        router = Router(llm)
        roles = Table(box=None, pad_edge=False)
        for column in ("role", "model", "options"):
            roles.add_column(column)
        for role in Role:
            route = router.route(role)
            options = ", ".join(f"{k}={v}" for k, v in sorted(route.options.items())) or "-"
            roles.add_row(role.value, route.name, Text(options))
        if llm.escalation is not None:
            to = router.route(Role.FIXER, failed_attempts=llm.escalation.after_failed_attempts)
            options = ", ".join(f"{k}={v}" for k, v in sorted(to.options.items())) or "-"
            roles.add_row(
                f"fixer after {llm.escalation.after_failed_attempts} failures",
                to.name,
                Text(options),
            )
        console.print(roles)
        local_only = "yes" if router.local_only() else "no"
        console.print(Text(f"local only (no code leaves this machine): {local_only}"))
    _problems(registry.problems)
