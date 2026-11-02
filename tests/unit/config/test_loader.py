from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from codecoremorph.config import (
    ConfigValidationError,
    IssueSeverity,
    MigrationConfig,
    config_hash,
    load_config,
    parse_config,
)
from codecoremorph.config.models import NetworkPolicy, NewWarningsPolicy, SandboxMode
from codecoremorph.core.errors import ConfigError, ExitCode
from codecoremorph.core.models import Role

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "cpp11-to-cpp20.yaml"

MINIMAL = """\
version: "1.0"
project: {name: demo}
migration:
  source: {language: cpp, standard: cpp11}
  target: {language: cpp, standard: cpp20}
"""


def _errors(text: str) -> list[str]:
    """Rendered issues of an invalid manifest."""
    with pytest.raises(ConfigValidationError) as info:
        parse_config(textwrap.dedent(text), "m.yaml")
    assert info.value.exit_code is ExitCode.CONFIG_OR_USAGE
    return info.value.render_issues()


def _single_error(text: str) -> str:
    errors = _errors(text)
    assert len(errors) == 1, errors
    return errors[0]


# --------------------------------------------------------------------------- valid manifests


def test_example_is_valid() -> None:
    loaded = load_config(EXAMPLE)
    cfg = loaded.config
    assert loaded.warnings == []
    assert cfg.project.name == "LegacyEngineCore"
    assert cfg.migration.target.rules == [
        "replace_typedefs_with_using",
        "convert_raw_pointers_to_smart_pointers",
        "use_concepts_for_templates",
    ]
    assert cfg.build["cmake"].configure_args == ["-DBUILD_TESTING=ON"]
    assert cfg.llm.roles[Role.SUMMARIZER].num_ctx == 32768
    assert cfg.llm.escalation is not None
    assert cfg.llm.escalation.to.effort == "xhigh"
    assert cfg.sandbox.network is NetworkPolicy.FETCH_ONLY
    assert len(loaded.config_hash) == 64


def test_minimal_manifest_gets_documented_defaults() -> None:
    cfg = parse_config(MINIMAL).config
    editor = cfg.llm.roles[Role.EDITOR]
    assert (editor.provider, editor.model) == ("anthropic", "claude-opus-5-5")
    assert editor.effort == "high"
    assert cfg.llm.for_role(Role.FIXER) == editor  # unset roles fall back to editor
    assert cfg.migration.base == "HEAD"
    assert cfg.guardrails.max_retry_budget == 3
    assert cfg.guardrails.rollback_on_failure is True
    assert cfg.sandbox.mode is SandboxMode.AUTO
    assert cfg.approvals.plan == "required"
    assert cfg.parity.characterization_tests.dir == "tests/codecoremorph_characterization"


def test_yaml_12_booleans_keep_off_as_a_string() -> None:
    cfg = parse_config(
        MINIMAL + "sandbox: {network: off}\nguardrails: {new_warnings: off}\n"
    ).config
    assert cfg.sandbox.network is NetworkPolicy.OFF
    assert cfg.guardrails.new_warnings is NewWarningsPolicy.OFF


def test_unquoted_version_number_is_accepted() -> None:
    assert parse_config(MINIMAL.replace('"1.0"', "1.0")).config.version == "1.0"


def test_paths_are_normalized() -> None:
    text = MINIMAL.replace("{name: demo}", "{name: demo, source_dirs: ['./src/', 'lib\\\\x']}")
    assert parse_config(text).config.project.source_dirs == ["src", "lib/x"]


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + MINIMAL.encode())
    assert load_config(path).config.project.name == "demo"


# --------------------------------------------------------------------------- PRD aliases


def test_root_path_alias() -> None:
    cfg = parse_config(MINIMAL.replace("{name: demo}", "{name: demo, root_path: ./src}")).config
    assert cfg.project.root == "./src"


def test_root_and_root_path_together_is_an_error() -> None:
    error = _single_error(MINIMAL.replace("{name: demo}", "{name: demo, root: a, root_path: b}"))
    assert error.startswith("m.yaml:2:1: error: project:")
    assert "not both" in error


@pytest.mark.parametrize(("flag", "mode"), [("true", "docker"), ("false", "native")])
def test_docker_sandbox_alias(flag: str, mode: str) -> None:
    loaded = parse_config(MINIMAL + f"guardrails:\n  docker_sandbox: {flag}\n", "m.yaml")
    assert loaded.config.sandbox.mode == mode
    [warning] = loaded.warnings
    assert warning.severity is IssueSeverity.WARNING
    assert warning.render("m.yaml") == (
        f"m.yaml:7:3: warning: guardrails.docker_sandbox: deprecated; "
        f"write `sandbox: {{mode: {mode}}}` instead"
    )


def test_docker_sandbox_conflicting_with_mode_is_an_error() -> None:
    error = _single_error(MINIMAL + "guardrails: {docker_sandbox: true}\nsandbox: {mode: native}\n")
    assert error.startswith("m.yaml:6:14: error: guardrails.docker_sandbox: conflicts with")


def test_prd_style_dict_validates_directly() -> None:
    cfg = MigrationConfig.model_validate(
        {
            "version": "1.0",
            "project": {"name": "LegacyEngineCore", "root_path": "./src"},
            "migration": {
                "source": {"language": "cpp", "standard": "cpp11", "build_system": "cmake"},
                "target": {"language": "cpp", "standard": "cpp20", "rules": ["r1"]},
            },
            "guardrails": {"rollback_on_failure": True, "docker_sandbox": True},
        }
    )
    assert cfg.project.root == "./src"
    assert cfg.sandbox.mode is SandboxMode.DOCKER


# --------------------------------------------------------------------------- invalid fields


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        # unknown key, with a suggestion
        (
            "guardrails:\n  max_retry_budgte: 3\n",
            "m.yaml:7:3: error: guardrails: unknown key `max_retry_budgte` "
            "(did you mean `max_retry_budget`?)",
        ),
        # wrong type
        (
            "guardrails:\n  max_retry_budget: lots\n",
            "m.yaml:7:21: error: guardrails.max_retry_budget: Input should be a valid integer, "
            "unable to parse string as an integer",
        ),
        # pattern mismatch, explained in words
        (
            "llm:\n  roles:\n    editor: {provider: Anthropic, model: m}\n",
            "m.yaml:8:24: error: llm.roles.editor.provider: must be a lowercase provider name, "
            "e.g. anthropic or ollama",
        ),
        # value out of range
        (
            "parity:\n  baseline_runs: 0\n",
            "m.yaml:7:18: error: parity.baseline_runs: Input should be greater than 0",
        ),
        # bad enum value
        (
            "sandbox:\n  mode: podman\n",
            "m.yaml:7:9: error: sandbox.mode: Input should be 'auto', 'docker' or 'native'",
        ),
        # bad mapping key
        (
            "llm:\n  roles:\n    editor: {provider: anthropic, model: m}\n"
            "    planner: {provider: anthropic, model: m}\n",
            "m.yaml:9:5: error: llm.roles.planner: invalid key: Input should be 'editor', "
            "'fixer', 'test_writer' or 'summarizer'",
        ),
        # cross-field rule inside a section
        (
            "llm:\n  roles:\n    fixer: {provider: anthropic, model: m}\n",
            "m.yaml:7:3: error: llm.roles: `editor` is required, because unset roles fall back "
            "to it",
        ),
        # rollback can't be turned off
        (
            "guardrails:\n  rollback_on_failure: false\n",
            "m.yaml:7:24: error: guardrails.rollback_on_failure: must be true: failed units are "
            "always rolled back, so the migration branch stays green (NFR-2.2)",
        ),
        # temperature gets a targeted explanation
        (
            "llm:\n  roles:\n    editor: {provider: anthropic, model: m, temperature: 0.2}\n",
            "m.yaml:8:45: error: llm.roles.editor: unknown key `temperature`: sampling settings "
            "are not portable across providers (and Anthropic rejects them); put "
            "provider-specific options under `provider_options`",
        ),
        # image not pinned by digest
        (
            "sandbox:\n  image: ubuntu:24.04\n",
            "m.yaml:7:10: error: sandbox.image: 'ubuntu:24.04' must be pinned by digest, e.g. "
            "registry/codecoremorph-cpp@sha256:<64 hex>",
        ),
        # absolute path where a relative one is required
        (
            "parity:\n  characterization_tests: {dir: /tmp/x}\n",
            "m.yaml:7:33: error: parity.characterization_tests.dir: '/tmp/x' must be relative to "
            "project.root",
        ),
    ],
)
def test_invalid_field_classes(snippet: str, expected: str) -> None:
    assert _single_error(MINIMAL + snippet) == expected


def test_git_revision_cannot_look_like_an_option() -> None:
    error = _single_error(MINIMAL.replace("migration:\n", "migration:\n  base: --exec=x\n"))
    assert error.startswith("m.yaml:4:9: error: migration.base: '--exec=x' is not a git revision")


def test_duplicate_rules() -> None:
    text = MINIMAL.replace("standard: cpp20}", "standard: cpp20, rules: [a, b, a]}")
    assert "rules listed more than once: a" in _single_error(text)


def test_missing_required_keys() -> None:
    errors = _errors('version: "1.0"\nproject: {root: .}\n')
    assert errors == [
        "m.yaml: error: required key `migration` is missing",
        "m.yaml:2:1: error: project: required key `name` is missing",
    ]


def test_unsupported_version() -> None:
    error = _single_error(MINIMAL.replace('"1.0"', '"2.0"'))
    assert "unsupported manifest version '2.0'" in error


def test_all_errors_are_reported_together() -> None:
    errors = _errors(
        """\
        version: "1.0"
        project: {name: demo, extra: 1}
        migration:
          source: {language: cpp, standard: cpp11}
          target: {language: cpp, standard: cpp20}
        guardrails:
          max_unit_minutes: -1
          rollback_on_failure: false
        sandbox:
          network: offline
        """
    )
    assert [e.split(": error:")[0] for e in errors] == [
        "m.yaml:2:23",
        "m.yaml:7:21",
        "m.yaml:8:24",
        "m.yaml:10:12",
    ]


# --------------------------------------------------------------------------- YAML-level problems


def test_yaml_syntax_error_has_a_location() -> None:
    error = _single_error('version: "1.0"\nproject: [\n')
    assert error.startswith("m.yaml:3:1: error: YAML syntax error:")


def test_duplicate_keys() -> None:
    errors = _errors(MINIMAL + "project: {name: again}\n")
    assert errors == [
        "m.yaml:6:1: error: project: duplicate key `project` (first defined on line 2)"
    ]


def test_empty_file() -> None:
    assert "the file is empty" in _single_error("# nothing but a comment\n")


def test_top_level_must_be_a_mapping() -> None:
    assert "top level must be a mapping" in _single_error("- a\n- b\n")


def test_secret_keys_are_rejected() -> None:
    error = _single_error(
        MINIMAL + "llm:\n  providers:\n    anthropic: {api_key: not-a-real-key}\n"
    )
    assert error.startswith("m.yaml:8:17: error: llm.providers.anthropic.api_key: credentials")


def test_secret_values_are_rejected_without_echoing_them() -> None:
    secret = "sk-ant-api03-" + "Q" * 30
    error = _single_error(MINIMAL.replace("{name: demo}", f"{{name: {secret}}}"))
    assert error.startswith("m.yaml:2:17: error: project.name: this value looks like a credential")
    assert secret not in error


# --------------------------------------------------------------------------- files and hashing


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found") as info:
        load_config(tmp_path / "nope.yaml")
    assert info.value.hint is not None


def test_directory_instead_of_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(MINIMAL.replace("demo", "d\xe9mo").encode("latin-1"))
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


def test_hash_ignores_comments_and_key_order() -> None:
    reordered = """\
# a comment
migration:
  target: {standard: cpp20, language: cpp}
  source: {standard: cpp11, language: cpp}
project: {name: demo}
version: "1.0"
"""
    assert parse_config(MINIMAL).config_hash == parse_config(reordered).config_hash
    changed = parse_config(MINIMAL.replace("demo", "other"))
    assert changed.config_hash != parse_config(MINIMAL).config_hash
    assert config_hash(parse_config(MINIMAL).config) == parse_config(MINIMAL).config_hash
