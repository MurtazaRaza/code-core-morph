"""Load rule packs: directories of rule YAML files plus Markdown guidance (SPEC §7.3).

Layout of a pack directory::

    pack.yaml                      name, language, version, minimum tool versions
    <rule_id>.yaml                 one rule per file; the file name is the rule ID
    guidance/<rule_id>.md          LLM guidance, referenced from the rule
    examples/<rule_id>/            before/after examples, referenced from the rule

A directory without ``pack.yaml`` (a project's ``migration.rules_paths`` entry) is a pack of
loose rules named after the directory.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError

from codecoremorph.config import ConfigValidationError, load_yaml12
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import CoreModel, LoadedRule, Rule

PACK_FILE = "pack.yaml"


class RulePack(CoreModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.:/-]*$")
    language: str
    version: str = "0"
    description: str = ""
    min_tool_versions: dict[str, str] = Field(
        default_factory=dict[str, str],
        description="e.g. {clang-tidy: '18'}: checks the pack uses need at least these versions",
    )


class RuleLoadError(ConfigError):
    """A rule or pack file is malformed."""


def _read_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuleLoadError(f"can't read {path}", reason=str(exc)) from exc
    try:
        return load_yaml12(text, source=str(path))
    except ConfigValidationError as exc:
        raise RuleLoadError(
            f"{path} is not valid YAML", reason="; ".join(exc.render_issues())
        ) from None


def _validation_reason(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False):
        where = ".".join(str(part) for part in error["loc"]) or "(top level)"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def load_rule(path: Path, pack: str) -> LoadedRule:
    data = _read_yaml(path)
    try:
        rule = Rule.model_validate(data)
    except ValidationError as exc:
        raise RuleLoadError(
            f"invalid rule in {path}",
            reason=_validation_reason(exc),
            hint="See SPEC §7.3 for the rule format.",
        ) from None
    if rule.id != path.stem:
        raise RuleLoadError(
            f"{path} defines rule {rule.id!r}",
            reason="A rule file must be named after the rule it defines.",
            hint=f"Rename the file to {rule.id}.yaml, or change its id to {path.stem!r}.",
        )
    loaded = LoadedRule(rule=rule, pack=pack, source=path)
    guidance = loaded.guidance_path()
    if guidance is not None and not guidance.is_file():
        raise RuleLoadError(
            f"rule {rule.id} names guidance file {rule.llm.guidance if rule.llm else ''}, "
            f"which doesn't exist",
            reason=f"looked for {guidance}",
        )
    return loaded


def load_pack(directory: Path) -> tuple[RulePack, list[LoadedRule]]:
    """Load a pack directory (or a directory of loose rules)."""
    if not directory.is_dir():
        raise RuleLoadError(
            f"rule directory {directory} doesn't exist",
            hint="Check migration.rules_paths in the manifest.",
        )
    pack_file = directory / PACK_FILE
    if pack_file.is_file():
        try:
            pack = RulePack.model_validate(_read_yaml(pack_file))
        except ValidationError as exc:
            raise RuleLoadError(
                f"invalid rule pack {pack_file}", reason=_validation_reason(exc)
            ) from None
    else:
        pack = RulePack(name=f"project:{directory.name.lower()}", language="any")
    rules = [
        load_rule(path, pack.name)
        for path in sorted(directory.glob("*.yaml"))
        if path.name != PACK_FILE
    ]
    for loaded in rules:
        if pack.language != "any" and loaded.rule.language != pack.language:
            raise RuleLoadError(
                f"rule {loaded.rule.id} is for {loaded.rule.language}, "
                f"but pack {pack.name} is for {pack.language}"
            )
    return pack, rules
