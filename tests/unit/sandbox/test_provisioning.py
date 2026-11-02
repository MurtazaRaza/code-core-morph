"""Toolchain image resolution (FR-1.2)."""

from __future__ import annotations

from typing import Any

import pytest
from docker.errors import DockerException, ImageNotFound

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.models import ToolchainSpec
from codecoremorph.sandbox import provisioning
from codecoremorph.sandbox.provisioning import ImageLock, parse_lock, read_lock, resolve_image

SPEC = ToolchainSpec(language="cpp", image="codecoremorph-cpp")
DIGEST = "sha256:" + "a" * 64


def test_parse_lock() -> None:
    lock = parse_lock(f"# comment\nimage=ghcr.io/x/codecoremorph-cpp\ndigest={DIGEST}\n", "cpp")
    assert lock.pinned_reference == f"ghcr.io/x/codecoremorph-cpp@{DIGEST}"
    assert lock.local_reference == "codecoremorph-cpp:local"
    unpublished = parse_lock("image=ghcr.io/x/codecoremorph-cpp\ndigest=\n", "cpp")
    assert unpublished.pinned_reference is None
    with pytest.raises(SandboxError, match="malformed digest"):
        parse_lock("image=x\ndigest=sha256:short\n", "cpp")
    with pytest.raises(SandboxError, match="no image="):
        parse_lock("digest=\n", "cpp")


def test_the_shipped_cpp_lock_is_readable() -> None:
    lock = read_lock("cpp")
    assert lock.image.endswith("codecoremorph-cpp")


class FakeImages:
    def __init__(self, ids: dict[str, str]):
        self.ids = ids

    def get(self, reference: str) -> Any:
        if reference not in self.ids:
            raise ImageNotFound(reference)
        return type("Image", (), {"id": self.ids[reference]})()


class FakeClient:
    def __init__(self, ids: dict[str, str]):
        self.images = FakeImages(ids)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _locked(monkeypatch: pytest.MonkeyPatch, digest: str | None) -> None:
    lock = ImageLock(language="cpp", image="ghcr.io/x/codecoremorph-cpp", digest=digest)
    monkeypatch.setattr(provisioning, "read_lock", lambda language: lock)


def test_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    override = f"registry/custom@{DIGEST}"
    assert resolve_image(SPEC, override) == override

    _locked(monkeypatch, DIGEST)
    assert resolve_image(SPEC, None) == f"ghcr.io/x/codecoremorph-cpp@{DIGEST}"

    _locked(monkeypatch, None)
    client = FakeClient({"codecoremorph-cpp:local": "sha256:" + "b" * 64})
    assert resolve_image(SPEC, None, client_factory=lambda: client) == "sha256:" + "b" * 64
    assert client.closed

    with pytest.raises(SandboxError, match="no cpp toolchain image is available") as error:
        resolve_image(SPEC, None, client_factory=lambda: FakeClient({}))
    assert "scripts/build-image cpp" in str(error.value.hint)

    def unreachable() -> FakeClient:
        raise DockerException("daemon down")

    with pytest.raises(SandboxError, match="can't reach Docker"):
        resolve_image(SPEC, None, client_factory=unreachable)
