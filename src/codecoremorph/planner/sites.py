"""Rule detectors, run over the project's files (SPEC §7.3, §7.5.1 step 3).

A rule applies to the in-scope files of its own language. With a detector, only files where it
finds sites get units; without one (codemod-only or natural-language-only rules), every such
file does (FR-9.3).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import DetectorType, LoadedRule, RuleSite
from codecoremorph.planner.project import ProjectModel
from codecoremorph.rules.applicability import StandardOrder, active_patterns
from codecoremorph.rules.detectors import find_pattern_sites

# Answers an adapter_query detector: (query name, files) -> sites.
QueryFn = Callable[[str, list[str]], list[RuleSite]]
# A hook detector: (worktree, files) -> sites.
HookFn = Callable[[Path, list[str]], Iterable[RuleSite]]


@dataclass(frozen=True)
class RuleScan:
    loaded: LoadedRule
    automatic: bool  # the standard bump: runs without being listed
    eligible: list[str]  # in-scope files of the rule's language, before the skip list
    sites: dict[str, list[RuleSite]]  # files with sites; empty without a detector

    @property
    def rule_id(self) -> str:
        return self.loaded.rule.id

    @property
    def has_detector(self) -> bool:
        return self.loaded.rule.detector is not None

    @property
    def site_count(self) -> int | None:
        if not self.has_detector:
            return None
        return sum(len(s) for s in self.sites.values())

    def targets(self) -> list[str]:
        """The files the rule would change: those with sites, or every eligible file."""
        return sorted(self.sites) if self.has_detector else list(self.eligible)


def load_hook(spec: str) -> HookFn:
    """Import ``module:function``. Only installed modules import: the project being migrated
    is not on ``sys.path``, so its code never runs on the host."""
    module_name, _, function = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
        hook = getattr(module, function)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(
            f"can't load the detector hook {spec}",
            reason=str(exc),
            hint="Install the package that provides it, or fix the rule's detector.hook.",
        ) from exc
    if not callable(hook):
        raise ConfigError(f"the detector hook {spec} is not a function")
    return cast(HookFn, hook)


def find_sites(
    loaded: LoadedRule,
    files: Sequence[str],
    *,
    worktree: Path,
    read: Callable[[str], str | None],
    order: StandardOrder,
    source: str,
    target: str,
    query: QueryFn,
    language_of: Callable[[str], str | None] | None = None,
) -> dict[str, list[RuleSite]]:
    """The rule's detector sites in ``files``, by file, in line order. Empty without a
    detector. A ``files`` detector (D-60) makes each of them that exists a site, those of the
    rule's language only when ``language_of`` tells."""
    detector = loaded.rule.detector
    found: list[RuleSite] = []
    if detector is None:
        pass
    elif detector.type is DetectorType.FILES:
        language = loaded.rule.language
        for path in files:
            if language_of is not None and language_of(path) not in (None, language):
                continue
            if (worktree / path).is_file():
                found.append(
                    RuleSite(file=path, line=1, text="still here: delete it once it is ported")
                )
    elif detector.type is DetectorType.PATTERN:
        patterns = active_patterns(order, detector, source, target)
        if patterns:
            for path in files:
                text = read(path)
                if text is not None:
                    found += find_pattern_sites(path, text, patterns)
    elif detector.type is DetectorType.ADAPTER_QUERY and detector.query is not None:
        found = query(detector.query, list(files))
    elif detector.hook is not None:
        found = list(load_hook(detector.hook)(worktree, list(files)))
    wanted = set(files)
    sites: dict[str, list[RuleSite]] = {}
    for site in found:
        if site.file in wanted:
            sites.setdefault(site.file, []).append(site)
    for file_sites in sites.values():
        file_sites.sort(key=lambda s: (s.line, s.column))
    return dict(sorted(sites.items()))


def scan_rule(
    loaded: LoadedRule,
    project: ProjectModel,
    *,
    order: StandardOrder,
    source: str,
    target: str,
    query: QueryFn,
    automatic: bool = False,
) -> RuleScan:
    rule = loaded.rule
    eligible = sorted(p for p, e in project.files.items() if e.language == rule.language)
    sites = find_sites(
        loaded,
        eligible,
        worktree=project.worktree,
        read=project.text,
        order=order,
        source=source,
        target=target,
        query=query,
    )
    return RuleScan(loaded=loaded, automatic=automatic, eligible=eligible, sites=sites)
