"""``codecoremorph init``: write a starter manifest for the repository (FR-1.4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.cli.output import handle_errors, status_label, stderr, stdout
from codecoremorph.config import (
    DEFAULT_CONFIG_NAME,
    ConfigValidationError,
    IssueSeverity,
    locate_issues,
    parse_config,
)
from codecoremorph.config.starter import StarterManifest, StarterRule, render_manifest
from codecoremorph.core.errors import UsageError, VcsError
from codecoremorph.core.models import ProjectDetection, RuleKind
from codecoremorph.rules.applicability import StandardOrder, check_migration, rule_excludes
from codecoremorph.rules.registry import RuleRegistry
from codecoremorph.vcs.repository import Repository

_KIND_ORDER = {RuleKind.API_PRESERVING: 0, RuleKind.API_CHANGING: 1, RuleKind.PROJECT: 2}


def _detect(
    adapters: AdapterRegistry, root: Path, files: list[str], language: str | None
) -> ProjectDetection:
    if language is not None:
        adapters.get(language)  # fails with the installed adapters listed
    found: list[ProjectDetection] = []
    for name, info in sorted(adapters.adapters.items()):
        if language is not None and name != language:
            continue
        detection = info.adapter().detect_project(root, files)
        if detection is not None:
            found.append(detection)
    if not found:
        installed = ", ".join(sorted(adapters.adapters)) or "none"
        raise UsageError(
            f"found no code in {root} that CodeCoreMorph can migrate",
            reason=f"no installed language adapter recognized the project (installed: {installed})",
            hint="Run it in a repository of a supported language (`codecoremorph adapters list`).",
        )
    found.sort(key=lambda d: (-d.code_files, d.language))
    chosen = found[0]
    others = [d.language for d in found[1:]]
    if others and language is None:
        stderr().print(
            Text(
                f"note: also found {', '.join(others)} code; this manifest migrates "
                f"{chosen.language} (pick another with --language)",
                style="yellow",
            )
        )
    if chosen.build_system is None:
        raise UsageError(
            f"found {chosen.language} code, but no build system CodeCoreMorph supports",
            reason=" ".join(chosen.notes) or None,
            hint="CodeCoreMorph builds and tests the project to check every change.",
        )
    return chosen


def _rules(
    registry: RuleRegistry, order: StandardOrder, source: str, target: str
) -> list[StarterRule]:
    applicable = [
        loaded.rule
        for loaded in registry.for_language(order.language)
        if not loaded.rule.applies_to.standard_bump
        and rule_excludes(order, loaded.rule, source, target) is None
    ]
    applicable.sort(key=lambda rule: (_KIND_ORDER.get(rule.kind, 3), rule.id))
    return [StarterRule(rule.id, rule.description) for rule in applicable]


def _root_from(manifest_dir: Path, root: Path) -> str:
    try:
        relative = Path(os.path.relpath(root, manifest_dir))
    except ValueError:  # another drive on Windows
        return root.as_posix()
    return relative.as_posix()


@handle_errors
def init_command(
    directory: Annotated[
        Path,
        typer.Argument(
            help="Where to write migration_config.yaml; the project is the git repository "
            "around it.",
            show_default=True,
        ),
    ] = Path(),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the manifest to this file instead."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(
            "--target", "-t", help="The target standard, e.g. cpp20 (default: suggested)."
        ),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option("--language", "-l", help="The language to migrate, if there are several."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    print_only: Annotated[
        bool, typer.Option("--stdout", help="Print the manifest instead of writing it.")
    ] = False,
) -> None:
    """Write a starter manifest: detects the language, build system, current standard and
    layout, and lists the rules that apply. Reads the repository; changes nothing else."""
    repo = Repository.discover(directory.resolve())
    files = repo.tracked_files()
    if not files:
        raise VcsError(
            f"git tracks no files in {repo.root}",
            reason="A run starts from a commit, and init reads the files git tracks.",
            hint="Commit your code first: `git add -A` and `git commit`.",
        )
    adapters = AdapterRegistry.discover()
    detection = _detect(adapters, repo.root, files, language)
    caps = adapters.get(detection.language).capabilities()
    order = StandardOrder(detection.language, tuple(caps.standards))
    goal = target or detection.target_standard
    if not order.known(goal):
        raise UsageError(
            f"{goal!r} is not a {detection.language} standard",
            hint=f"Use one of: {', '.join(order.standards)}.",
        )
    if order.newer(detection.standard, goal):
        raise UsageError(
            f"the target {goal} is older than the project's {detection.standard}",
            reason="CodeCoreMorph upgrades code; it never moves it to an older standard.",
            hint=f"Pick {detection.standard} or newer with --target.",
        )
    rules = RuleRegistry.discover()

    path = (output or directory / DEFAULT_CONFIG_NAME).resolve()
    starter = StarterManifest(
        file_name=path.name,
        name=detection.name or repo.root.name,
        root=_root_from(path.parent, repo.root),
        language=detection.language,
        build_system=detection.build_system or "",
        source=detection.standard,
        source_note=detection.standard_note,
        target=goal,
        targets=[s for s in order.standards if order.at_least(s, detection.standard)],
        rules=_rules(rules, order, detection.standard, goal),
        source_dirs=detection.source_dirs,
        test_dirs=detection.test_dirs,
        exclude=detection.exclude,
        public_api=detection.public_api,
        configure_args=[(a.arg, a.reason) for a in detection.configure_args],
        notes=detection.notes,
    )
    text = render_manifest(starter)

    # A starter that doesn't validate is a bug in init, never the user's to fix.
    loaded = parse_config(text, source=str(path))
    problems = check_migration(loaded.config, caps.standards, rules)
    issues = locate_issues(text, [(p.loc, p.message, IssueSeverity(p.severity)) for p in problems])
    if any(i.severity is IssueSeverity.ERROR for i in issues):
        raise ConfigValidationError(str(path), issues)

    if print_only:
        typer.echo(text, nl=False)
        return
    if path.exists() and not force:
        raise UsageError(
            f"{path} already exists",
            hint="Pass --force to replace it, or --output to write somewhere else.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))

    console = stdout()
    console.print(Text.assemble(status_label("ok"), f" wrote {path}"))
    console.print(
        Text(
            f"     {starter.name}: {detection.language} {detection.standard} "
            f"({detection.standard_note}) -> {goal}; {len(starter.rules)} rules"
        )
    )
    console.print(
        Text(
            f"     sources: {', '.join(starter.source_dirs) or '-'}; "
            f"tests: {', '.join(starter.test_dirs) or 'none found'}"
        )
    )
    for note in detection.notes:
        console.print(Text.assemble(status_label("warn"), f" {note}"))
    shown = _shown(path)
    console.print(Text("next: read it through (the comments say what was detected), then"))
    console.print(Text(f"  codecoremorph doctor {shown}"))
    console.print(Text(f"  codecoremorph plan {shown}"))
    console.print(
        Text(f"  or with Claude Code: codecoremorph skill install --project {_shown(repo.root)}")
    )


def _shown(path: Path) -> str:
    """``path`` relative to the current directory when it is below it."""
    try:
        return str(path.relative_to(Path.cwd().resolve())) or "."
    except ValueError:
        return str(path)
