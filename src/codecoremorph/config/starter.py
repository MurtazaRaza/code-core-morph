"""The starter manifest that ``codecoremorph init`` writes (FR-1.4).

Detected settings come first, each with a comment saying what it is and, for the standard,
where it came from. The main options follow at their defaults, so a new user sees them.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass

from codecoremorph.config.models import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    SCHEMA_VERSION,
    ApprovalsConfig,
    CharacterizationTestsConfig,
    GuardrailsConfig,
    SandboxConfig,
)

_COMMENT_COLUMN = 37
_LINE_WIDTH = 100


@dataclass(frozen=True)
class StarterRule:
    id: str
    description: str


@dataclass(frozen=True)
class StarterManifest:
    """Everything the starter manifest says. Paths are ``project.*`` paths."""

    file_name: str
    name: str
    root: str
    language: str
    build_system: str
    source: str
    source_note: str
    target: str
    targets: Sequence[str]  # the standards the target may be, oldest first
    rules: Sequence[StarterRule]
    source_dirs: Sequence[str]
    test_dirs: Sequence[str]
    exclude: Sequence[str]
    public_api: Sequence[str]
    configure_args: Sequence[tuple[str, str]] = ()  # (argument, reason)
    notes: Sequence[str] = ()


def _q(value: str) -> str:
    """A YAML double-quoted scalar (JSON's string syntax is valid YAML 1.2)."""
    return json.dumps(value, ensure_ascii=False)


def _flow(items: Sequence[str]) -> str:
    return "[" + ", ".join(_q(item) for item in items) + "]"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _line(text: str, comment: str | None = None) -> str:
    if not comment:
        return text
    pad = max(_COMMENT_COLUMN - len(text), 1)
    return f"{text}{' ' * pad}# {_one_line(comment)}"


def _short(description: str, width: int) -> str:
    """A rule description's first sentence, cut at a word to fit ``width``."""
    first = _one_line(description).split(". ", 1)[0].rstrip(".")
    if len(first) <= width:
        return first
    cut = textwrap.shorten(first, width=max(width, 20), placeholder="")
    return cut.rstrip(" ,;:(") + "..."


def _rule_line(rule: StarterRule) -> str:
    text = f"      - {rule.id}"
    room = _LINE_WIDTH - max(len(text) + 1, _COMMENT_COLUMN) - 2
    return _line(text, _short(rule.description, room))


def _range(values: Sequence[str]) -> str:
    if len(values) <= 2:
        return " or ".join(values)
    return f"{values[0]} to {values[-1]}"


def render_manifest(m: StarterManifest) -> str:
    """The manifest's text, LF line endings, ending with a newline."""
    guardrails = GuardrailsConfig()
    sandbox = SandboxConfig()
    approvals = ApprovalsConfig()
    characterization = CharacterizationTestsConfig()
    cost = "null" if guardrails.max_run_cost_usd is None else f"{guardrails.max_run_cost_usd}"
    lines = [
        "# CodeCoreMorph migration manifest, written by `codecoremorph init`.",
        f"# Check it with `codecoremorph validate {m.file_name}`. Settings left out keep their",
        "# defaults; `codecoremorph schema` lists them all.",
    ]
    if m.notes:
        lines += ["#", "# Check before the first run:"]
        for note in m.notes:
            wrapped = textwrap.wrap(_one_line(note), width=90)
            lines.append(f"#   - {wrapped[0]}")
            lines += [f"#     {rest}" for rest in wrapped[1:]]
    lines += [
        "",
        f'version: "{SCHEMA_VERSION}"',
        "",
        "project:",
        _line(f"  name: {_q(m.name)}"),
        _line(f"  root: {_q(m.root)}", "the git repository root, relative to this file"),
        _line(f"  source_dirs: {_flow(m.source_dirs)}", "the code to migrate"),
        _line(f"  test_dirs: {_flow(m.test_dirs)}", "the tests: directories or file patterns"),
        _line(f"  exclude: {_flow(m.exclude)}", "never edited: vendored or generated code"),
        _line(f"  public_api: {_flow(m.public_api)}", "declarations here are the public API"),
        "",
        "migration:",
        _line("  base: HEAD", "the commit to start from"),
        f"  # {m.source}: {_one_line(m.source_note)}",
        f"  source: {{language: {m.language}, standard: {m.source}, "
        f"build_system: {m.build_system}}}",
        "  target:",
        f"    language: {m.language}",
        _line(f"    standard: {m.target}", f"{_range(m.targets)}; the standard bump runs first"),
    ]
    if m.rules:
        lines.append(_line("    rules:", "applied in this order; delete those you don't want"))
        lines += [_rule_line(rule) for rule in m.rules]
    else:
        lines.append(_line("    rules: []", "`codecoremorph rules list` shows them"))
    lines += [
        _line("  rules_paths: []", "extra project-local rule directories"),
        "",
        "build:",
        f"  {m.build_system}:",
    ]
    if m.configure_args:
        lines.append("    configure_args:")
        for arg, reason in m.configure_args:
            lines += [f"      # {_one_line(reason)}", f"      - {_q(arg)}"]
    else:
        lines.append(_line("    configure_args: []", "extra configure flags"))
    lines += [
        "",
        "# The model of a plain `codecoremorph run`. A run with Claude Code as the agent",
        "# (`codecoremorph skill install`, then `codecoremorph run --host-agent`) calls none.",
        "llm:",
        _line("  roles:", "unset roles use the editor's model"),
        f"    editor: {{provider: {DEFAULT_PROVIDER}, model: {DEFAULT_MODEL}, "
        f"effort: {DEFAULT_EFFORT}}}",
        "",
        "guardrails:",
        _line(
            f"  max_retry_budget: {guardrails.max_retry_budget}",
            "retries after a unit's first attempt",
        ),
        _line(f"  max_run_cost_usd: {cost}", "API providers only; null means no cap"),
        _line(f"  max_unit_minutes: {guardrails.max_unit_minutes}", "model time per unit"),
        _line(
            f"  test_file_edits: {guardrails.test_file_edits.value}",
            "deny | review | allow: edits to existing tests",
        ),
        "",
        "sandbox:",
        _line(
            f"  mode: {sandbox.mode.value}", "auto | docker | native: auto uses Docker if running"
        ),
        "",
        "approvals:",
        _line(f"  plan: {approvals.plan.value}", "required | auto: review the plan first"),
        _line(f"  unit: {approvals.unit.value}", "none | api_changes | test_changes | all"),
        "",
        "parity:",
        "  characterization_tests:",
        _line(
            f"    enabled: {'true' if characterization.enabled else 'false'}",
            "write tests for code no test reaches, before the plan",
        ),
    ]
    return "\n".join(lines) + "\n"
