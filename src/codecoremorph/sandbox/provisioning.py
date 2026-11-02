"""Toolchain images: which one a run uses, and checking it is pinned (FR-1.2, SPEC §7.8).

``images/<lang>/image.lock`` records the published image and its digest as ``KEY=VALUE``
lines (the ``pull-images`` scripts read the same file). Until an image is published (CI is
deferred, D-28), the lock has no digest and runs use a local build made by
``scripts/build-image``, referenced by its immutable image ID.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.models import ToolchainSpec

LOCAL_TAG = "local"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def images_dir() -> Path:
    for candidate in (_PACKAGE_ROOT / "_images", _PACKAGE_ROOT.parents[1] / "images"):
        if candidate.is_dir():
            return candidate
    raise SandboxError("the toolchain image definitions are missing from this installation")


@dataclass(frozen=True)
class ImageLock:
    language: str
    image: str  # repository, e.g. ghcr.io/codecoremorph/codecoremorph-cpp
    digest: str | None

    @property
    def pinned_reference(self) -> str | None:
        return f"{self.image}@{self.digest}" if self.digest else None

    @property
    def local_reference(self) -> str:
        return f"{self.image.rsplit('/', 1)[-1]}:{LOCAL_TAG}"


def parse_lock(text: str, language: str) -> ImageLock:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    image = values.get("image", "")
    digest = values.get("digest") or None
    if not image:
        raise SandboxError(f"images/{language}/image.lock has no image= line")
    if digest is not None and not _DIGEST.match(digest):
        raise SandboxError(
            f"images/{language}/image.lock has a malformed digest {digest!r}",
            hint="A digest looks like sha256:<64 hex digits>.",
        )
    return ImageLock(language=language, image=image, digest=digest)


def read_lock(language: str) -> ImageLock:
    path = images_dir() / language / "image.lock"
    if not path.is_file():
        raise SandboxError(f"no toolchain image is defined for {language}", reason=str(path))
    return parse_lock(path.read_text(encoding="utf-8"), language)


def resolve_image(
    spec: ToolchainSpec,
    override: str | None,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> str:
    """The image reference a run uses, always pinned by content (FR-1.2).

    ``sandbox.image`` wins (the manifest already requires a digest). Then the published,
    digest-pinned image from the lock. Then a local build, resolved to its image ID.
    """
    if override is not None:
        return override
    lock = read_lock(spec.language)
    if lock.pinned_reference is not None:
        return lock.pinned_reference
    if client_factory is None:
        from codecoremorph.sandbox.docker.executor import _docker_client  # pyright: ignore[reportPrivateUsage]

        client_factory = _docker_client
    from docker.errors import DockerException, ImageNotFound

    try:
        client = client_factory()
        try:
            image = client.images.get(lock.local_reference)
            return str(image.id)
        finally:
            client.close()
    except ImageNotFound:
        pass
    except DockerException as exc:
        raise SandboxError(
            "can't reach Docker to find the toolchain image", reason=str(exc)
        ) from exc
    raise SandboxError(
        f"no {spec.language} toolchain image is available",
        reason=f"{lock.image} isn't published yet and there is no local build "
        f"({lock.local_reference})",
        hint=f"Build it with scripts/build-image {spec.language} (.bat on Windows), or use "
        "sandbox.mode: native.",
    )
