"""Rule discovery: packs from the ``codecoremorph.rules`` entry point plus the project's
``migration.rules_paths`` (FR-9.1, FR-9.3, FR-15.1).

An entry point names a pack directory: a ``Path`` (or a zero-argument callable returning one).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LoadedRule
from codecoremorph.rules.loader import RuleLoadError, RulePack, load_pack

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "codecoremorph.rules"


def _pack_dir(loaded: object) -> Path | None:
    if isinstance(loaded, Path):
        return loaded
    if callable(loaded):
        result: object = loaded()
        return result if isinstance(result, Path) else None
    return None


@dataclass
class RuleRegistry:
    packs: dict[str, RulePack] = field(default_factory=dict[str, RulePack])
    rules: dict[str, LoadedRule] = field(default_factory=dict[str, LoadedRule])
    problems: list[str] = field(default_factory=list[str])

    @classmethod
    def discover(
        cls, rules_paths: Iterable[Path] = (), eps: list[EntryPoint] | None = None
    ) -> RuleRegistry:
        """Load the built-in and plugin packs, then the project's rule directories.

        A broken plugin pack is reported in ``problems``; a broken project rule is an error,
        because the project asked for it.
        """
        registry = cls()
        found = list(entry_points(group=ENTRY_POINT_GROUP)) if eps is None else eps
        for ep in sorted(found, key=lambda e: e.name):
            where = f"{ep.name} = {ep.value}"
            try:
                directory = _pack_dir(ep.load())
                if directory is None:
                    registry.problems.append(f"{where}: doesn't name a pack directory")
                    continue
                registry.add_pack(directory, plugin=True)
            except RuleLoadError as exc:
                registry.problems.append(f"{where}: {exc}")
            except Exception as exc:  # noqa: BLE001 - a broken plugin must not break the CLI
                registry.problems.append(f"{where}: can't be loaded ({type(exc).__name__}: {exc})")
        for directory in rules_paths:
            registry.add_pack(directory)
        return registry

    def add_pack(self, directory: Path, *, plugin: bool = False) -> RulePack:
        pack, rules = load_pack(directory, plugin=plugin)
        if pack.name in self.packs:
            raise RuleLoadError(f"rule pack {pack.name!r} is registered twice ({directory})")
        for loaded in rules:
            existing = self.rules.get(loaded.rule.id)
            if existing is not None:
                raise RuleLoadError(
                    f"rule {loaded.rule.id!r} is defined twice",
                    reason=f"in {existing.source} ({existing.pack}) and {loaded.source}",
                    hint="Rename one of them; rule IDs are global.",
                )
        self.packs[pack.name] = pack
        for loaded in rules:
            self.rules[loaded.rule.id] = loaded
        log.debug("rule pack %s: %d rules from %s", pack.name, len(rules), directory)
        return pack

    def get(self, rule_id: str) -> LoadedRule:
        loaded = self.rules.get(rule_id)
        if loaded is None:
            reason = f"known rules: {', '.join(sorted(self.rules)) or 'none'}"
            if self.problems:
                reason += "; rule packs that didn't load: " + "; ".join(self.problems)
            raise ConfigError(
                f"unknown rule {rule_id!r}",
                reason=reason,
                hint="Check migration.target.rules, or add the rule's directory to "
                "migration.rules_paths.",
            )
        return loaded

    def for_language(self, language: str) -> list[LoadedRule]:
        return [r for r in self.rules.values() if r.rule.language == language]

    def resolve(self, rule_ids: Iterable[str]) -> list[LoadedRule]:
        return [self.get(rule_id) for rule_id in rule_ids]

    def pack_of(self, loaded: LoadedRule) -> RulePack:
        return self.packs[loaded.pack]


PackDirectory = Path | Callable[[], Path]
