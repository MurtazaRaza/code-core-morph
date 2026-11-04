"""Which rules, detector patterns and codemod passes apply to a migration (SPEC §7.3).

Standards compare in the order the language adapter lists them
(``AdapterCapabilities.standards``), so nothing here knows any language's versions
(NFR-3.1). The planner uses these functions to choose units; ``codecoremorph validate`` uses
:func:`check_migration` to reject manifests that can't work.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from codecoremorph.config.models import MigrationConfig
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import CodemodSpec, Detector, Rule, RuleKind, StandardRange
from codecoremorph.rules.registry import RuleRegistry

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class StandardOrder:
    """A language's standards, oldest first."""

    language: str
    standards: tuple[str, ...]

    def known(self, standard: str) -> bool:
        return standard in self.standards

    def rank(self, standard: str) -> int:
        try:
            return self.standards.index(standard)
        except ValueError:
            raise ConfigError(
                f"{standard!r} is not a {self.language} standard",
                hint=f"Use one of: {', '.join(self.standards) or '(none listed)'}.",
            ) from None

    def at_least(self, standard: str, minimum: str) -> bool:
        return self.rank(standard) >= self.rank(minimum)

    def newer(self, standard: str, than: str) -> bool:
        return self.rank(standard) > self.rank(than)


def range_excludes(
    order: StandardOrder, item: StandardRange, source: str, target: str
) -> str | None:
    """Why ``item`` doesn't apply to ``source`` → ``target``, or None when it applies."""
    if item.min_target_standard is not None and not order.at_least(
        target, item.min_target_standard
    ):
        return f"needs a target standard of {item.min_target_standard} or newer"
    if item.max_source_standard is not None and order.newer(source, item.max_source_standard):
        return f"only applies to sources up to {item.max_source_standard}"
    return None


def rule_excludes(
    order: StandardOrder,
    rule: Rule,
    source: str,
    target: str,
    *,
    target_language: str | None = None,
) -> str | None:
    """Why ``rule`` doesn't apply to ``source`` → ``target``, or None when it applies.

    ``target_language`` is the migration's target language when it differs from the source
    (a port, D-56): then only port rules to that language apply, and ``target`` is a standard
    of the target language, which the source's ``order`` doesn't rank."""
    if rule.language != order.language:
        return f"is a {rule.language} rule, not a {order.language} one"
    porting = target_language is not None and target_language != order.language
    if rule.kind is RuleKind.PORT:
        if not porting or rule.target_language != target_language:
            return (
                f"ports {rule.language} to {rule.target_language}, so it applies only when "
                f"migration.target.language is {rule.target_language}"
            )
        maximum = rule.applies_to.max_source_standard
        if maximum is not None and order.newer(source, maximum):
            return f"only applies to sources up to {maximum}"
        return None
    if porting:
        return (
            f"is a {rule.kind.value} rule; a migration from {order.language} to "
            f"{target_language} takes port rules"
        )
    if rule.applies_to.standard_bump and not order.newer(target, source):
        return "only applies when the target standard is newer than the source"
    return range_excludes(order, rule.applies_to, source, target)


def active_patterns(
    order: StandardOrder, detector: Detector, source: str, target: str
) -> list[str]:
    """The detector patterns that count for ``source`` → ``target``."""
    return [
        spec.pattern
        for spec in detector.pattern_specs()
        if range_excludes(order, spec, source, target) is None
    ]


def codemod_applies(order: StandardOrder, spec: CodemodSpec, target: str) -> bool:
    """A pass that parses under a standard newer than the target would introduce code the
    target can't compile, so it is skipped."""
    return spec.standard is None or order.at_least(target, spec.standard)


def unknown_standards(order: StandardOrder, rule: Rule) -> list[str]:
    """Standards the rule names that the language adapter doesn't know."""
    named = list(rule.applies_to.standards())
    if rule.detector is not None:
        for spec in rule.detector.pattern_specs():
            named += spec.standards()
    named += [c.standard for c in rule.codemods if c.standard is not None]
    return sorted({s for s in named if not order.known(s)})


def standard_bump_rules(registry: RuleRegistry, language: str) -> list[str]:
    """Rules that run first, automatically, whenever the target standard is newer."""
    return sorted(
        rule_id
        for rule_id, loaded in registry.rules.items()
        if loaded.rule.language == language and loaded.rule.applies_to.standard_bump
    )


@dataclass(frozen=True)
class MigrationProblem:
    """A manifest problem found by :func:`check_migration`, located by key path."""

    loc: tuple[str | int, ...]
    message: str
    severity: Severity = "error"


def check_migration(
    config: MigrationConfig,
    standards: Sequence[str] | None,
    registry: RuleRegistry,
    target_standards: Sequence[str] | None = None,
) -> list[MigrationProblem]:
    """Check the manifest's standards and rules against the adapter and the rule packs.

    ``standards`` is the source language adapter's list, or None when no adapter for the
    language is installed (then only the rule IDs are checked). A port (the target language
    differs, D-56) checks the target standard against ``target_standards``, the target
    language adapter's list.
    """
    if config.migration.target.language != config.migration.source.language:
        return _check_port(config, standards, registry, target_standards)
    migration = config.migration
    source, target = migration.source, migration.target
    problems: list[MigrationProblem] = []
    order: StandardOrder | None = None
    if standards is None:
        problems.append(
            MigrationProblem(
                ("migration", "source", "language"),
                f"no {source.language} adapter is installed, so the standards and rules "
                "weren't checked (see `codecoremorph adapters list`)",
                "warning",
            )
        )
    elif not standards:
        problems.append(
            MigrationProblem(
                ("migration", "source", "language"),
                f"the {source.language} adapter doesn't list its standards, so the standards "
                "and rule conditions weren't checked",
                "warning",
            )
        )
    elif target.language == source.language:
        order = StandardOrder(source.language, tuple(standards))
        hint = f"; {source.language} standards, oldest first: {', '.join(standards)}"
        for side, standard in (("source", source.standard), ("target", target.standard)):
            if not order.known(standard):
                problems.append(
                    MigrationProblem(
                        ("migration", side, "standard"),
                        f"{standard!r} is not a standard the {source.language} adapter "
                        f"supports{hint}",
                    )
                )
        if any(p.severity == "error" for p in problems):
            order = None
        elif order.newer(source.standard, target.standard):
            problems.append(
                MigrationProblem(
                    ("migration", "target", "standard"),
                    f"the target {target.standard} is older than the source {source.standard}; "
                    "CodeCoreMorph only migrates to the same or a newer standard",
                )
            )
            order = None

    bump_rules = standard_bump_rules(registry, source.language)
    for index, rule_id in enumerate(target.rules):
        loc = ("migration", "target", "rules", index)
        loaded = registry.rules.get(rule_id)
        if loaded is None:
            known = ", ".join(sorted(r.rule.id for r in registry.for_language(target.language)))
            problems.append(
                MigrationProblem(
                    loc,
                    f"unknown rule {rule_id!r}; known {target.language} rules: "
                    f"{known or 'none'} (add a project rule directory to "
                    "migration.rules_paths)",
                )
            )
            continue
        if rule_id in bump_rules:
            problems.append(
                MigrationProblem(
                    loc,
                    f"{rule_id} runs automatically, first, whenever the target standard is "
                    "newer than the source; remove it from the list",
                    "warning",
                )
            )
            continue
        if order is None:
            continue
        unknown = unknown_standards(order, loaded.rule)
        if unknown:
            problems.append(
                MigrationProblem(
                    loc,
                    f"{rule_id} ({loaded.source}) names standards the {order.language} adapter "
                    f"doesn't know: {', '.join(unknown)}",
                )
            )
            continue
        reason = rule_excludes(order, loaded.rule, source.standard, target.standard)
        if reason is not None:
            problems.append(
                MigrationProblem(
                    loc,
                    f"{rule_id} doesn't apply to {source.standard} -> {target.standard}: it "
                    f"{reason}",
                )
            )

    if order is not None:
        if order.newer(target.standard, source.standard) and not bump_rules:
            problems.append(
                MigrationProblem(
                    ("migration", "target", "standard"),
                    f"no installed {source.language} rule raises the standard (a rule with "
                    "applies_to.standard_bump), so the build files keep the source standard",
                    "warning",
                )
            )
        if source.standard == target.standard and not target.rules:
            problems.append(
                MigrationProblem(
                    ("migration", "target"),
                    "the source and target standards are the same and no rules are listed, "
                    "so there is nothing to migrate",
                    "warning",
                )
            )
    return problems


def _standard_problem(
    side: str, language: str, standard: str, standards: Sequence[str] | None
) -> MigrationProblem | None:
    loc = ("migration", side, "standard")
    if standards is None:
        return MigrationProblem(
            ("migration", side, "language"),
            f"no {language} adapter is installed, so the {side} standard wasn't checked (see "
            "`codecoremorph adapters list`)",
            "warning",
        )
    if standards and standard not in standards:
        return MigrationProblem(
            loc,
            f"{standard!r} is not a standard the {language} adapter supports; {language} "
            f"standards, oldest first: {', '.join(standards)}",
        )
    return None


def _check_port(
    config: MigrationConfig,
    standards: Sequence[str] | None,
    registry: RuleRegistry,
    target_standards: Sequence[str] | None,
) -> list[MigrationProblem]:
    """A port (D-56): each side's standard against its own adapter, and port rules only."""
    source, target = config.migration.source, config.migration.target
    problems = [
        p
        for p in (
            _standard_problem("source", source.language, source.standard, standards),
            _standard_problem("target", target.language, target.standard, target_standards),
        )
        if p is not None
    ]
    order = (
        StandardOrder(source.language, tuple(standards))
        if standards and not any(p.loc[1] == "source" and p.severity == "error" for p in problems)
        else None
    )
    ports = sorted(
        r.rule.id
        for r in registry.rules.values()
        if r.rule.kind is RuleKind.PORT
        and r.rule.language == source.language
        and r.rule.target_language == target.language
    )
    if not target.rules:
        problems.append(
            MigrationProblem(
                ("migration", "target", "rules"),
                f"a migration from {source.language} to {target.language} needs a port rule; "
                f"installed: {', '.join(ports) or 'none'}",
            )
        )
    for index, rule_id in enumerate(target.rules):
        loc = ("migration", "target", "rules", index)
        loaded = registry.rules.get(rule_id)
        if loaded is None:
            problems.append(
                MigrationProblem(
                    loc,
                    f"unknown rule {rule_id!r}; {source.language} to {target.language} port "
                    f"rules: {', '.join(ports) or 'none'}",
                )
            )
            continue
        rule = loaded.rule
        if rule.kind is not RuleKind.PORT or rule.target_language != target.language:
            problems.append(
                MigrationProblem(
                    loc,
                    f"{rule_id} isn't a port rule to {target.language}; a migration from "
                    f"{source.language} to {target.language} takes those only: "
                    f"{', '.join(ports) or 'none'}",
                )
            )
            continue
        if order is None:
            continue
        unknown = unknown_standards(order, rule)
        reason = (
            f"names standards the {source.language} adapter doesn't know: {', '.join(unknown)}"
            if unknown
            else rule_excludes(
                order, rule, source.standard, target.standard, target_language=target.language
            )
        )
        if reason is not None:
            problems.append(
                MigrationProblem(loc, f"{rule_id} doesn't apply to this migration: it {reason}")
            )
    return problems
