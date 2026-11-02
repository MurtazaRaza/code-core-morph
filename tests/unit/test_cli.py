from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecoremorph import __version__
from codecoremorph.cli import app, main
from codecoremorph.cli import doctor as doctor_module
from codecoremorph.core.errors import HostEnvironmentError
from codecoremorph.sandbox.docker.probe import DockerProbe

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "cpp11-to-cpp20.yaml"
runner = CliRunner()

pytestmark = pytest.mark.usefixtures("restore_logging")


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"codecoremorph {__version__}"


def test_validate_example() -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "is a valid manifest (version 1.0)" in result.output
    assert "LegacyEngineCore: cpp cpp11 -> cpp cpp20" in result.output


def test_validate_lists_every_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        'version: "1.0"\n'
        "project: {name: x, colour: blue}\n"
        "migration:\n"
        "  source: {language: cpp, standard: cpp11}\n"
        "  target: {language: cpp, standard: cpp20, rules: ['rules[0]']}\n"
        "guardrails: {rollback_on_failure: false}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(broken)])
    assert result.exit_code == 2
    assert f"{broken}:2:20: error: project: unknown key `colour`" in result.output
    assert f"{broken}:5:52: error: migration.target.rules[0]: must be a lowercase" in result.output
    assert f"{broken}:6:35: error: guardrails.rollback_on_failure: must be true" in result.output
    assert f"error: {broken} has 3 errors" in result.output


def test_validate_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "not found" in result.output
    assert "next:" in result.output


def test_validate_shows_warnings(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        'version: "1.0"\nproject: {name: x}\nmigration:\n'
        "  source: {language: cpp, standard: cpp11}\n"
        "  target: {language: cpp, standard: cpp20}\n"
        "guardrails: {docker_sandbox: true}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "warning: guardrails.docker_sandbox: deprecated" in result.output


def test_validate_checks_standards_and_rules_against_the_plugins(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        'version: "1.0"\nproject: {name: x}\nmigration:\n'
        "  source: {language: cpp, standard: cpp20}\n"
        "  target:\n"
        "    language: cpp\n"
        "    standard: cpp17\n"
        "    rules: [no_such_rule]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2, result.output
    assert f"{path}:7:15: error: migration.target.standard: the target cpp17 is older" in (
        result.output
    )
    assert f"{path}:8:13: error: migration.target.rules[0]: unknown rule 'no_such_rule'" in (
        result.output
    )
    assert f"error: {path} has 2 errors" in result.output


def test_validate_rejects_a_rule_the_target_standard_cant_use(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        'version: "1.0"\nproject: {name: x}\nmigration:\n'
        "  source: {language: cpp, standard: cpp98}\n"
        "  target: {language: cpp, standard: cpp17, rules: [use_concepts_for_templates]}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2, result.output
    assert "use_concepts_for_templates doesn't apply to cpp98 -> cpp17" in result.output

    path.write_text(path.read_text().replace("cpp17", "cpp23"), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "x: cpp cpp98 -> cpp cpp23" in result.output


def test_schema_to_stdout_and_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert json.loads(result.output)["$schema"].startswith("https://json-schema.org/")
    out = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["title"] == (
        "CodeCoreMorph migration manifest"
    )


@pytest.fixture
def fake_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module, "git_version", lambda: (2, 43, 0))
    monkeypatch.setattr(
        doctor_module,
        "probe_docker",
        lambda: DockerProbe(reachable=True, server_version="27.0.1", os_type="linux"),
    )


@pytest.mark.usefixtures("fake_environment")
def test_doctor_all_good() -> None:
    result = runner.invoke(app, ["doctor", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "git" in result.output
    assert "27.0.1, linux containers" in result.output
    assert "is valid" in result.output


@pytest.mark.usefixtures("fake_environment")
def test_doctor_without_docker_only_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor_module, "probe_docker", lambda: DockerProbe(reachable=False, detail="no pipe")
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "native mode" in result.output


@pytest.mark.usefixtures("fake_environment")
def test_doctor_fails_on_old_or_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module, "git_version", lambda: (2, 20, 1))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "need >= 2.30" in result.output

    def no_git() -> tuple[int, int, int]:
        raise HostEnvironmentError("git was not found on PATH", hint="Install git")

    monkeypatch.setattr(doctor_module, "git_version", no_git)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "git was not found on PATH" in result.output


@pytest.mark.usefixtures("fake_environment")
def test_doctor_reports_invalid_config(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: '1.0'\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor", str(bad)])
    assert result.exit_code == 3
    assert "2 error(s)" in result.output


def test_python_check() -> None:
    assert doctor_module.check_python((3, 12, 0)).status == "ok"
    assert doctor_module.check_python((3, 11, 9)).status == "fail"


def test_docker_check_windows_containers() -> None:
    probe = DockerProbe(reachable=True, server_version="27.0.1", os_type="windows")
    result = doctor_module.check_docker(probe)
    assert result.status == "warn"
    assert result.hint is not None
    assert "Linux containers" in result.hint


def test_unknown_command_is_a_usage_error() -> None:
    assert runner.invoke(app, ["no-such-command"]).exit_code == 2


def test_main_maps_internal_errors_to_exit_10(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(path: Path) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("codecoremorph.cli.validate.load_config", explode)
    with pytest.raises(SystemExit) as info:
        main(["validate", str(EXAMPLE)])
    assert info.value.code == 10
    err = capsys.readouterr().err
    assert "internal error: RuntimeError: kaboom" in err
    assert "Traceback" in err


def test_main_success_exits_zero() -> None:
    with pytest.raises(SystemExit) as info:
        main(["validate", str(EXAMPLE)])
    assert info.value.code == 0


def test_docker_details_and_bind_mount_warnings() -> None:
    probe = DockerProbe(
        reachable=True,
        server_version="27.3.1",
        os_type="linux",
        operating_system="Docker Desktop",
        cpus=8,
        memory_bytes=16 * 2**30,
    )
    assert doctor_module.check_docker(probe).detail == (
        "27.3.1, linux containers, 8 CPUs, 16.0 GiB, Docker Desktop"
    )
    windows = doctor_module.check_bind_mounts(probe, platform="win32")
    assert windows is not None
    assert windows.status == "warn"
    assert "WSL2" in (windows.hint or "")
    mac = doctor_module.check_bind_mounts(probe, platform="darwin")
    assert mac is not None
    assert mac.status == "info"
    assert doctor_module.check_bind_mounts(probe, platform="linux") is None
    assert doctor_module.check_bind_mounts(DockerProbe(reachable=False), "win32") is None


# --------------------------------------------------------------------------- Phase 2 commands


def test_help_lists_the_command_groups() -> None:
    result = runner.invoke(app, ["--help"])
    for group in ("adapters", "rules", "toolchain", "debug"):
        assert group in result.output


def test_adapters_list() -> None:
    result = runner.invoke(app, ["adapters", "list"])
    assert result.exit_code == 0, result.output
    assert "CppAdapter" in result.output


def test_rules_list_includes_project_rules(tmp_path: Path) -> None:
    rules = tmp_path / "my-rules"
    rules.mkdir()
    (rules / "house_style.yaml").write_text(
        "id: house_style\nlanguage: cpp\nkind: api_preserving\ndescription: Our style.\n"
        "llm: {guidance: style.md}\n",
        encoding="utf-8",
    )
    (rules / "style.md").write_text("Be consistent.\n", encoding="utf-8")
    config = tmp_path / "migration_config.yaml"
    config.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("rules_paths: []", "rules_paths: [my-rules]"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["rules", "list", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "replace_typedefs_with_using" in result.output
    assert "house_style  cpp, api_preserving, llm (pack project:my-rules)" in result.output
    only_java = runner.invoke(app, ["rules", "list", "--language", "java"])
    assert "bump_standard" not in only_java.output


def test_toolchain_path_and_list(isolated_home: Path) -> None:
    result = runner.invoke(app, ["toolchain", "path"])
    assert result.output.strip() == str(isolated_home / "toolchains")
    result = runner.invoke(app, ["toolchain", "path", "cpp"])
    assert result.output.strip() == str(isolated_home / "toolchains" / "cpp.env")
    result = runner.invoke(app, ["toolchain", "list"])
    assert result.exit_code == 0, result.output
    assert "cpp: cmake" in result.output
    assert "not installed" in result.output
    assert "codecoremorph toolchain install cpp" in result.output


def test_toolchain_install_rejects_unknown_languages() -> None:
    result = runner.invoke(app, ["toolchain", "install", "cobol"])
    assert result.exit_code == 3
    assert "available: cpp" in result.output


def test_debug_commands_report_a_missing_manifest(tmp_path: Path) -> None:
    for step in ("build", "test", "index"):
        result = runner.invoke(app, ["debug", step, str(tmp_path / "missing.yaml")])
        assert result.exit_code == 2, result.output
