"""The .NET toolchain the C# adapter reads from ``csharp.env``, and its command lines
(SPEC §11.3, D-65).

Every command runs with the run's own .NET home, NuGet package folder and HTTP cache (in the
adapter's cache, never the user's profile), in English (the diagnostics are parsed), with
telemetry off and no build server or MSBuild node left running after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from codecoremorph.core.errors import SandboxError

INSTALL_HINT = (
    "Install the pinned native toolchain with `codecoremorph toolchain install csharp` "
    "(or scripts/install-toolchain csharp), or use Docker (sandbox.mode: docker)."
)
_REQUIRED = (
    "CODECOREMORPH_DOTNET",
    "CODECOREMORPH_DOTNET_SDK",
    "CODECOREMORPH_DOTNET_REF_PACK",
    "CODECOREMORPH_NUGET_CONFIG",
)
# MSBuild switches every build, restore and test passes: no node reuse or shared compiler
# server (nothing outlives the command), no logo, the console logger plain and unwrapped.
MSBUILD_SWITCHES = (
    "-nologo",
    "-nodeReuse:false",
    "-p:UseSharedCompilation=false",
    "-tl:off",
    "-clp:NoSummary;ForceNoAlign",
    # Only the framework packs a project uses (the spike's restore had fetched the ASP.NET and
    # desktop ones as well).
    "-p:DisableTransitiveFrameworkReferenceDownloads=true",
)


@dataclass(frozen=True)
class DotnetToolchain:
    """The toolchain's paths as the sandbox sees them, from ``csharp.env``."""

    dotnet: str  # the dotnet host
    sdk: str  # the SDK's directory (sdk/<version>)
    ref_pack: str  # the newest framework's reference assemblies
    nuget_config: str  # nuget.org alone
    windows: bool  # paths are Windows paths (native mode on Windows)

    @classmethod
    def from_env(cls, env: dict[str, str], *, windows: bool) -> DotnetToolchain:
        missing = [name for name in _REQUIRED if not env.get(name)]
        if missing:
            raise SandboxError(
                "the C# toolchain environment is incomplete",
                reason="csharp.env doesn't set " + ", ".join(missing),
                hint=INSTALL_HINT,
            )
        return cls(
            dotnet=env["CODECOREMORPH_DOTNET"],
            sdk=env["CODECOREMORPH_DOTNET_SDK"],
            ref_pack=env["CODECOREMORPH_DOTNET_REF_PACK"],
            nuget_config=env["CODECOREMORPH_NUGET_CONFIG"],
            windows=windows,
        )

    def join(self, base: str, *parts: str) -> str:
        path = PureWindowsPath(base) if self.windows else PurePosixPath(base)
        return str(path.joinpath(*parts))

    @property
    def roslyn(self) -> str:
        """The SDK's own Roslyn: ``csc.dll`` and the ``Microsoft.CodeAnalysis`` assemblies."""
        return self.join(self.sdk, "Roslyn", "bincore")


def run_environment(home: str, packages: str, http_cache: str, temp: str) -> dict[str, str]:
    """The variables of every ``dotnet`` command of a run (sandbox paths)."""
    return {
        "DOTNET_CLI_HOME": home,
        "NUGET_PACKAGES": packages,
        "NUGET_HTTP_CACHE_PATH": http_cache,
        "NUGET_PLUGINS_CACHE_PATH": f"{http_cache}-plugins",
        "DOTNET_CLI_UI_LANGUAGE": "en",
        "MSBUILDDISABLENODEREUSE": "1",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_MULTILEVEL_LOOKUP": "0",
        "TEMP": temp,
        "TMP": temp,
        "TMPDIR": temp,
    }
