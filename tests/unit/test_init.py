"""`codecoremorph init`: the starter manifest (FR-1.4)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecoremorph.cli import app
from codecoremorph.config import parse_config
from codecoremorph.config.starter import StarterManifest, StarterRule, render_manifest

GitRunner = Callable[..., str]
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "cpp"
runner = CliRunner()

pytestmark = pytest.mark.usefixtures("restore_logging")


@pytest.fixture
def project(tmp_path: Path, git: GitRunner) -> Callable[[str], Path]:
    """A committed copy of a fixture, as its own repository."""

    def make(fixture: str) -> Path:
        root = tmp_path / fixture
        shutil.copytree(FIXTURES / fixture, root)
        git(tmp_path, "init", "--quiet", str(root))
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "-m", "initial")
        return root

    return make


# The standard each fixture is written in (SPEC §12); cpp98-legacy sets none in its build.
FIXTURE_STANDARDS = {
    "ownership": "cpp11",
    "typedefs": "cpp11",
    "templates": "cpp11",
    "removed-features": "cpp11",
    "c-compat": "cpp11",
    "multi-module": "cpp11",
    "cpp98-legacy": "cpp98",
    "cpp17-legacy": "cpp17",
    "untested": "cpp11",
    "injection": "cpp11",
}


@pytest.mark.parametrize("fixture", sorted(FIXTURE_STANDARDS))
def test_every_fixture_gets_a_valid_manifest(fixture: str, project: Callable[[str], Path]) -> None:
    root = project(fixture)
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    manifest = root / "migration_config.yaml"
    assert "wrote" in result.output

    validated = runner.invoke(app, ["validate", str(manifest)])
    assert validated.exit_code == 0, validated.output

    cfg = parse_config(manifest.read_text(encoding="utf-8")).config
    assert cfg.project.root == "."
    assert cfg.migration.source.standard == FIXTURE_STANDARDS[fixture]
    assert cfg.migration.source.build_system == "cmake"
    assert cfg.migration.target.standard == "cpp20"
    assert cfg.project.test_dirs == ["tests"]
    assert cfg.project.public_api == ["include/**"]
    assert set(cfg.project.source_dirs) >= {"include", "src"}
    assert "modernize_basics" in cfg.migration.target.rules
    assert "bump_standard" not in cfg.migration.target.rules  # it runs first by itself


def test_the_guessed_standard_is_called_out(project: Callable[[str], Path]) -> None:
    root = project("cpp98-legacy")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    assert "WARN migration.source.standard is a guess (cpp98)" in result.output
    text = (root / "migration_config.yaml").read_text(encoding="utf-8")
    assert "#   - migration.source.standard is a guess (cpp98)" in text
    assert "# cpp98: guessed: the build files set no standard" in text


def test_target_option(project: Callable[[str], Path]) -> None:
    root = project("ownership")
    result = runner.invoke(app, ["init", str(root), "--target", "cpp23", "--stdout"])
    assert result.exit_code == 0, result.output
    cfg = parse_config(result.output).config
    assert cfg.migration.target.standard == "cpp23"
    assert not (root / "migration_config.yaml").exists()  # --stdout writes nothing


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("cpp98", "the target cpp98 is older than the project's cpp11"),
        ("cpp26", "'cpp26' is not a cpp standard"),
    ],
)
def test_bad_targets(project: Callable[[str], Path], target: str, message: str) -> None:
    root = project("ownership")
    result = runner.invoke(app, ["init", str(root), "--target", target])
    assert result.exit_code == 2
    assert message in result.output


def test_rules_that_need_a_newer_target_are_left_out(project: Callable[[str], Path]) -> None:
    root = project("ownership")
    result = runner.invoke(app, ["init", str(root), "--target", "cpp17", "--stdout"])
    assert result.exit_code == 0, result.output
    rules = parse_config(result.output).config.migration.target.rules
    assert "use_concepts_for_templates" not in rules  # needs a C++20 target
    assert "replace_typedefs_with_using" in rules


def test_never_overwrites_without_force(project: Callable[[str], Path]) -> None:
    root = project("typedefs")
    manifest = root / "migration_config.yaml"
    manifest.write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 2
    assert "already exists" in result.output
    assert manifest.read_text(encoding="utf-8") == "# mine\n"

    forced = runner.invoke(app, ["init", str(root), "--force"])
    assert forced.exit_code == 0, forced.output
    assert manifest.read_text(encoding="utf-8").startswith("# CodeCoreMorph migration manifest")


def test_output_elsewhere_points_back_at_the_repository(
    project: Callable[[str], Path], tmp_path: Path
) -> None:
    root = project("typedefs")
    out = root / "config" / "upgrade.yaml"
    result = runner.invoke(app, ["init", str(root), "--output", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert parse_config(text).config.project.root == ".."
    assert "codecoremorph validate upgrade.yaml" in text
    validated = runner.invoke(app, ["validate", str(out)])
    assert validated.exit_code == 0, validated.output


def test_reads_only_what_git_tracks(project: Callable[[str], Path]) -> None:
    root = project("typedefs")
    (root / "build" / "generated").mkdir(parents=True)
    (root / "build" / "generated" / "x.cpp").write_text("auto [a, b] = f();\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root), "--stdout"])
    assert result.exit_code == 0, result.output
    cfg = parse_config(result.output).config
    assert "build" not in cfg.project.source_dirs
    assert cfg.migration.source.standard == "cpp11"


def test_not_a_repository(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 3
    assert "is not inside a git working tree" in result.output


def test_nothing_tracked(tmp_path: Path, git: GitRunner) -> None:
    git(tmp_path, "init", "--quiet", str(tmp_path / "empty"))
    result = runner.invoke(app, ["init", str(tmp_path / "empty")])
    assert result.exit_code == 3
    assert "git tracks no files" in result.output


def test_no_supported_code(tmp_path: Path, git: GitRunner) -> None:
    root = tmp_path / "py"
    root.mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    git(tmp_path, "init", "--quiet", str(root))
    git(root, "add", "--all")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 2
    assert "found no code" in result.output


def test_cpp_without_cmake(tmp_path: Path, git: GitRunner) -> None:
    root = tmp_path / "make"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.cpp").write_text("int main() {}\n", encoding="utf-8")
    (root / "Makefile").write_text("all:\n", encoding="utf-8")
    git(tmp_path, "init", "--quiet", str(root))
    git(root, "add", "--all")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 2
    assert "no build system CodeCoreMorph supports" in result.output
    assert "CMake only" in result.output


# --------------------------------------------------------------------------- rendering


def starter(**changes: object) -> StarterManifest:
    fields: dict[str, object] = {
        "file_name": "migration_config.yaml",
        "name": "demo",
        "root": ".",
        "language": "cpp",
        "build_system": "cmake",
        "source": "cpp11",
        "source_note": "set in CMakeLists.txt",
        "target": "cpp20",
        "targets": ["cpp11", "cpp14", "cpp17", "cpp20", "cpp23"],
        "rules": [StarterRule("replace_typedefs_with_using", "Replace typedefs.")],
        "source_dirs": ["src"],
        "test_dirs": ["tests"],
        "exclude": [],
        "public_api": [],
    }
    fields.update(changes)
    return StarterManifest(**fields)  # type: ignore[arg-type]


def test_render_round_trips() -> None:
    text = render_manifest(starter())
    cfg = parse_config(text).config
    assert cfg.project.name == "demo"
    assert cfg.migration.target.rules == ["replace_typedefs_with_using"]
    assert cfg.build["cmake"].configure_args == []
    assert text.endswith("\n")
    assert "\r" not in text


def test_render_quotes_what_yaml_would_misread() -> None:
    text = render_manifest(
        starter(name='odd: "name" # here', source_dirs=["src", "a b"], exclude=["*.pb.cc"])
    )
    cfg = parse_config(text).config
    assert cfg.project.name == 'odd: "name" # here'
    assert cfg.project.source_dirs == ["src", "a b"]
    assert cfg.project.exclude == ["*.pb.cc"]


def test_render_configure_args_with_reasons() -> None:
    text = render_manifest(starter(configure_args=[("-DX_TESTS=ON", "the tests\nneed it")]))
    assert '      # the tests need it\n      - "-DX_TESTS=ON"\n' in text
    assert parse_config(text).config.build["cmake"].configure_args == ["-DX_TESTS=ON"]


def test_render_without_rules() -> None:
    cfg = parse_config(render_manifest(starter(rules=[]))).config
    assert cfg.migration.target.rules == []


def test_long_rule_descriptions_are_cut_at_a_word() -> None:
    rule = StarterRule(
        "convert_raw_pointers_to_smart_pointers",
        "Replace owning raw pointers (members, locals and return values that own memory) "
        "with std::unique_ptr, and much more besides.",
    )
    line = next(
        line for line in render_manifest(starter(rules=[rule])).splitlines() if rule.id in line
    )
    assert len(line) <= 100
    assert line.endswith("...")
    assert ",..." not in line
