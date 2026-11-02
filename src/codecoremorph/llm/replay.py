"""Record and replay LLM calls, so tests never call live models (FR-8.4).

A cassette is a JSON file of request/result pairs keyed by a hash of the normalized request.
Normalizing makes the key independent of the platform: line endings in prompts (file
contents differ between Windows and Linux checkouts) and dict ordering don't change it.
:class:`ReplayProvider` serves results in recorded order and fails loudly on a miss.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from codecoremorph.core.errors import ReplayMissError
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    CoreModel,
    HealthStatus,
    ModelCapabilities,
)
from codecoremorph.llm.base import LLMProvider, close_provider

CASSETTE_VERSION = 1
Kind = Literal["complete", "count_tokens"]


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_normalize(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def request_key(request: CompletionRequest, kind: Kind = "complete") -> str:
    payload = {"kind": kind, "request": _normalize(request.model_dump(mode="json"))}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CassetteEntry(CoreModel):
    key: str
    kind: Kind = "complete"
    model: str
    last_message: str = Field(default="", description="An excerpt, for reading diffs")
    result: CompletionResult | None = None
    tokens: int | None = None


class CassetteFile(CoreModel):
    version: int = CASSETTE_VERSION
    entries: list[CassetteEntry] = Field(default_factory=list[CassetteEntry])


def _excerpt(request: CompletionRequest, limit: int = 160) -> str:
    message = request.messages[-1]
    text = " ".join(b.model_dump_json() for b in message.content)
    return text[:limit]


@dataclass
class Cassette:
    path: Path
    entries: list[CassetteEntry] = field(default_factory=list[CassetteEntry])

    @classmethod
    def load(cls, path: Path) -> Cassette:
        if not path.is_file():
            return cls(path)
        data = CassetteFile.model_validate_json(path.read_bytes())
        if data.version != CASSETTE_VERSION:
            raise ReplayMissError(
                f"cassette {path} is version {data.version}; this CodeCoreMorph reads "
                f"version {CASSETTE_VERSION}",
                hint="Record it again.",
            )
        return cls(path, list(data.entries))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = CassetteFile(entries=self.entries).model_dump_json(indent=2, exclude_none=True)
        self.path.write_bytes((text + "\n").encode("utf-8"))


class RecordingProvider:
    """Wraps a provider and writes every call to a cassette."""

    def __init__(self, inner: LLMProvider, cassette: Cassette):
        self.inner = inner
        self.name = inner.name
        self.cassette = cassette

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    def health_check(self, model: str | None = None) -> HealthStatus:
        return self.inner.health_check(model)

    def close(self) -> None:
        close_provider(self.inner)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        result = self.inner.complete(request)
        self.cassette.entries.append(
            CassetteEntry(
                key=request_key(request),
                model=request.model,
                last_message=_excerpt(request),
                result=result,
            )
        )
        self.cassette.save()
        return result

    def count_tokens(self, request: CompletionRequest) -> int:
        tokens = self.inner.count_tokens(request)
        self.cassette.entries.append(
            CassetteEntry(
                key=request_key(request, "count_tokens"),
                kind="count_tokens",
                model=request.model,
                last_message=_excerpt(request),
                tokens=tokens,
            )
        )
        self.cassette.save()
        return tokens


class ReplayProvider:
    """Serves a cassette. Identical requests get their recorded results in order; any other
    request raises :class:`ReplayMissError`."""

    def __init__(
        self, cassette: Cassette, name: str, *, capabilities: ModelCapabilities | None = None
    ):
        self.cassette = cassette
        self.name = name
        self._used: set[int] = set()
        self._capabilities = capabilities or ModelCapabilities(
            context_window=1_000_000, max_output_tokens=128_000, source="config"
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities

    def health_check(self, model: str | None = None) -> HealthStatus:
        return HealthStatus(ok=True, detail=f"replaying {self.cassette.path.name}")

    def _take(self, key: str, request: CompletionRequest) -> CassetteEntry:
        for i, entry in enumerate(self.cassette.entries):
            if i not in self._used and entry.key == key:
                self._used.add(i)
                return entry
        recorded = sum(1 for e in self.cassette.entries if e.key == key)
        raise ReplayMissError(
            f"cassette {self.cassette.path.name} has no response for this request",
            reason=(
                f"request {key[:12]} was replayed {recorded} times already"
                if recorded
                else f"no recorded request matches {key[:12]} ({len(self.cassette.entries)} "
                f"entries); the last message starts: {_excerpt(request, 120)}"
            ),
            hint="The prompts, tools or file contents changed since recording. Re-record the "
            "cassette (pytest --record-cassettes), and review the diff.",
        )

    def complete(self, request: CompletionRequest) -> CompletionResult:
        entry = self._take(request_key(request), request)
        if entry.result is None:
            raise ReplayMissError(f"cassette entry {entry.key[:12]} has no result")
        return entry.result

    def count_tokens(self, request: CompletionRequest) -> int:
        entry = self._take(request_key(request, "count_tokens"), request)
        return entry.tokens or 0

    @property
    def unused(self) -> int:
        return len(self.cassette.entries) - len(self._used)


Responder = CompletionResult | Callable[[CompletionRequest], CompletionResult]


class ScriptedProvider:
    """Returns scripted results in order: for tests, and for authoring cassettes without a
    live model. Every request is kept in ``requests``."""

    def __init__(
        self,
        responses: Sequence[Responder],
        *,
        name: str = "anthropic",
        capabilities: ModelCapabilities | None = None,
    ):
        self.name = name
        self.responses = list(responses)
        self.requests: list[CompletionRequest] = []
        self._capabilities = capabilities or ModelCapabilities(
            context_window=1_000_000, max_output_tokens=128_000, source="config"
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities

    def health_check(self, model: str | None = None) -> HealthStatus:
        return HealthStatus(ok=True, detail="scripted")

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        if not self.responses:
            raise ReplayMissError(
                f"the scripted provider ran out of responses after {len(self.requests) - 1}",
                reason=f"the last message starts: {_excerpt(request, 120)}",
            )
        response = self.responses.pop(0)
        result = response(request) if callable(response) else response
        return result.model_copy(update={"model": request.model})

    def count_tokens(self, request: CompletionRequest) -> int:
        return sum(len(m.model_dump_json()) for m in request.messages) // 3


def cassette_providers(
    names: Sequence[str],
    path: Path,
    *,
    record: bool,
    create: Callable[[str], LLMProvider],
) -> dict[str, LLMProvider]:
    """Providers that record to (or replay from) one cassette, for ``names``. Recording wraps
    the real providers ``create`` builds; replaying needs no provider at all."""
    cassette = Cassette.load(path) if not record else Cassette(path)
    if record:
        return {name: RecordingProvider(create(name), cassette) for name in names}
    return {name: ReplayProvider(cassette, name) for name in names}
