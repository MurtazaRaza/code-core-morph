"""The C# toolchain from csharp.env, its environment and the index's defines (D-65)."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.csharp.dotnet import DotnetToolchain, run_environment
from codecoremorph.adapters.csharp.indexing import framework_defines
from codecoremorph.core.errors import SandboxError

ENV = {
    "CODECOREMORPH_DOTNET": "/opt/ccm/toolchains/dotnet-sdk/10.0.403/dotnet",
    "CODECOREMORPH_DOTNET_SDK": "/opt/ccm/toolchains/dotnet-sdk/10.0.403/sdk/10.0.403",
    "CODECOREMORPH_DOTNET_REF_PACK": "/opt/ccm/packs/ref/net10.0",
    "CODECOREMORPH_NUGET_CONFIG": "/opt/ccm/toolchains/csharp/nuget.config",
}


def test_the_toolchain_comes_from_csharp_env() -> None:
    tools = DotnetToolchain.from_env(ENV, windows=False)
    assert tools.roslyn == "/opt/ccm/toolchains/dotnet-sdk/10.0.403/sdk/10.0.403/Roslyn/bincore"
    windows = DotnetToolchain.from_env(
        {**ENV, "CODECOREMORPH_DOTNET_SDK": r"E:\h\dotnet-sdk\10.0.403\sdk\10.0.403"}, windows=True
    )
    assert windows.roslyn == r"E:\h\dotnet-sdk\10.0.403\sdk\10.0.403\Roslyn\bincore"


def test_an_incomplete_env_says_what_is_missing() -> None:
    with pytest.raises(SandboxError, match="incomplete") as caught:
        DotnetToolchain.from_env({"CODECOREMORPH_DOTNET": "x"}, windows=False)
    assert "CODECOREMORPH_NUGET_CONFIG" in str(caught.value.reason)


def test_every_command_keeps_dotnet_and_nuget_in_the_run_s_cache() -> None:
    env = run_environment("/c/home", "/c/packages", "/c/http", "/c/tmp")
    assert env["DOTNET_CLI_HOME"] == "/c/home"
    assert env["NUGET_PACKAGES"] == "/c/packages"
    assert env["NUGET_HTTP_CACHE_PATH"] == "/c/http"
    assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert env["MSBUILDDISABLENODEREUSE"] == "1"
    assert env["DOTNET_CLI_UI_LANGUAGE"] == "en"
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == "/c/tmp"


@pytest.mark.parametrize(
    ("tfm", "present", "absent"),
    [
        (
            "net6.0",
            {"NET", "NET6_0", "NET5_0_OR_GREATER", "NET6_0_OR_GREATER", "NETCOREAPP"},
            {"NET7_0_OR_GREATER"},
        ),
        ("net8.0-windows", {"NET8_0", "NET8_0_OR_GREATER"}, {"NET9_0_OR_GREATER"}),
        ("netcoreapp3.1", {"NETCOREAPP3_1", "NETCOREAPP3_0_OR_GREATER"}, {"NET5_0_OR_GREATER"}),
        (
            "netstandard2.0",
            {"NETSTANDARD", "NETSTANDARD2_0", "NETSTANDARD1_6_OR_GREATER"},
            {"NETSTANDARD2_1"},
        ),
        ("net48", {"NETFRAMEWORK", "NET48"}, {"NET"}),
    ],
)
def test_framework_defines(tfm: str, present: set[str], absent: set[str]) -> None:
    defines = set(framework_defines(tfm))
    assert {"DEBUG", "TRACE"} <= defines
    assert present <= defines
    assert not absent & defines
