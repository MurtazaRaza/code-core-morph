"""Docker daemon reachability and details, for ``codecoremorph doctor`` and executor selection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DockerProbe(BaseModel):
    model_config = ConfigDict(frozen=True)

    reachable: bool
    server_version: str | None = None
    os_type: str | None = None  # "linux" or "windows" (Docker Desktop's container mode)
    operating_system: str | None = None  # e.g. "Docker Desktop", "Ubuntu 24.04 LTS"
    architecture: str | None = None
    cpus: int | None = None
    memory_bytes: int | None = None
    detail: str | None = None


def probe_docker(timeout_s: int = 5) -> DockerProbe:
    """Ask the Docker daemon for its version and resources. Never raises for an unreachable
    daemon."""
    # Imported here: the Docker SDK is slow to import, and most commands never need it.
    import docker
    from docker.errors import DockerException

    try:
        client = docker.from_env(timeout=timeout_s)
        try:
            version = client.version()
            info = client.info()
        finally:
            client.close()
    except DockerException as exc:
        return DockerProbe(reachable=False, detail=str(exc))

    def _int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    return DockerProbe(
        reachable=True,
        server_version=str(version.get("Version", "unknown")),
        os_type=str(version.get("Os", "")) or None,
        operating_system=str(info.get("OperatingSystem", "")) or None,
        architecture=str(info.get("Architecture", "")) or None,
        cpus=_int(info.get("NCPU")),
        memory_bytes=_int(info.get("MemTotal")),
    )
