"""Adapter discovery through the ``codecoremorph.adapters`` entry point (FR-15.1).

Built-in adapters register the same way as third-party ones, so the core never imports a
specific adapter (NFR-3.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points

from codecoremorph.adapters.base import LanguageAdapter
from codecoremorph.core.errors import UsageError
from codecoremorph.core.plugins import ADAPTER_API

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "codecoremorph.adapters"


@dataclass(frozen=True)
class AdapterInfo:
    language: str
    adapter: type[LanguageAdapter]
    entry_point: str
    distribution: str | None


@dataclass
class AdapterRegistry:
    adapters: dict[str, AdapterInfo] = field(default_factory=dict[str, AdapterInfo])
    problems: list[str] = field(default_factory=list[str])
    # Languages whose adapter was found but not loaded, with the reason (an API mismatch).
    rejected: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def discover(cls, eps: list[EntryPoint] | None = None) -> AdapterRegistry:
        """Load every registered adapter. Broken plugins are reported, not raised."""
        registry = cls()
        found = list(entry_points(group=ENTRY_POINT_GROUP)) if eps is None else eps
        for ep in sorted(found, key=lambda e: e.name):
            registry._load(ep)
        return registry

    def _load(self, ep: EntryPoint) -> None:
        where = f"{ep.name} = {ep.value}"
        try:
            loaded: object = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break the CLI
            self.problems.append(f"{where}: can't be imported ({type(exc).__name__}: {exc})")
            return
        if not (isinstance(loaded, type) and issubclass(loaded, LanguageAdapter)):
            self.problems.append(f"{where}: is not a LanguageAdapter subclass")
            return
        language = getattr(loaded, "language", None)
        problem = ADAPTER_API.problem(getattr(loaded, "api_version", None), where)
        if problem is not None:
            self.problems.append(problem)
            if isinstance(language, str) and language:
                self.rejected.setdefault(language, problem)
            return
        if not isinstance(language, str) or not language:
            self.problems.append(f"{where}: doesn't set `language`")
            return
        if language in self.adapters:
            self.problems.append(
                f"{where}: language {language!r} is already provided by "
                f"{self.adapters[language].entry_point}"
            )
            return
        distribution = ep.dist.name if ep.dist is not None else None
        self.adapters[language] = AdapterInfo(language, loaded, where, distribution)

    def get(self, language: str) -> LanguageAdapter:
        info = self.adapters.get(language)
        if info is None:
            if language in self.rejected:
                raise UsageError(
                    f"the {language} adapter can't be used",
                    reason=self.rejected[language],
                    hint="`codecoremorph adapters list` shows the adapters that loaded.",
                )
            available = ", ".join(sorted(self.adapters)) or "none"
            raise UsageError(
                f"no adapter is installed for language {language!r}",
                reason=f"installed adapters: {available}",
                hint="Check migration.source.language, or install the adapter plugin.",
            )
        return info.adapter()
