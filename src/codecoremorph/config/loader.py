"""Load and validate ``migration_config.yaml``, reporting every problem with its location (FR-1.1).

Validation runs in two passes, and the issues of both are reported together:

1. YAML: syntax, duplicate keys, credentials in the file (FR-1.6), deprecated aliases.
2. Schema: types, allowed values, unknown keys and cross-field rules, through pydantic.

The loader reads YAML 1.2 scalars: only ``true``/``false`` are booleans, so values like
``network: off`` stay strings, as the manifest reference intends.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import types
from pathlib import Path
from typing import Annotated, Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import ErrorDetails
from yaml.error import Mark
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from codecoremorph.config.issues import ConfigIssue, ConfigValidationError, IssueSeverity
from codecoremorph.config.models import (
    PATTERN_DESCRIPTIONS,
    Loc,
    MigrationConfig,
    SandboxMode,
)
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.secrets import looks_like_secret

DEFAULT_CONFIG_NAME = "migration_config.yaml"

_BOOL_TAG = "tag:yaml.org,2002:bool"
_DROPPED_TAGS = frozenset({_BOOL_TAG, "tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:value"})


class _Yaml12Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans and no implicit timestamps."""


_Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, regex) for tag, regex in resolvers if tag not in _DROPPED_TAGS]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(  # pyright: ignore[reportUnknownMemberType] - stub gap
    _BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)

_SECRET_KEY = re.compile(
    r"(?i)^(?:.*[_-])?(?:api[_-]?key|apikey|secret(?:[_-]key)?|password|passwd|token|"
    r"auth[_-]?token|access[_-]?token|credentials?)$"
)

_SAMPLING_HINT = (
    "sampling settings are not portable across providers (and Anthropic rejects them); "
    "put provider-specific options under `provider_options`"
)
_UNKNOWN_KEY_HINTS = dict.fromkeys(("temperature", "top_p", "top_k", "seed"), _SAMPLING_HINT)


class LoadedConfig(BaseModel):
    """A validated manifest plus its warnings and content hash (NFR-6.1)."""

    model_config = ConfigDict(frozen=True)

    source: str
    config: MigrationConfig
    warnings: list[ConfigIssue]
    config_hash: str


def load_config(path: Path) -> LoadedConfig:
    """Read and validate a manifest file. Raises :class:`ConfigValidationError` on any error."""
    source = str(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(
            f"config file {source} not found",
            hint=(
                f"Pass the path to your manifest, or create {DEFAULT_CONFIG_NAME}; "
                "examples/cpp11-to-cpp20.yaml is a starting point."
            ),
        ) from None
    except IsADirectoryError:
        raise ConfigError(f"{source} is a directory, not a config file") from None
    except OSError as exc:
        raise ConfigError(f"can't read {source}", reason=exc.strerror) from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{source} is not valid UTF-8",
            reason=f"invalid byte at offset {exc.start}",
            hint="Save the file as UTF-8.",
        ) from None
    return parse_config(text, source=source)


def parse_config(text: str, source: str = "<string>") -> LoadedConfig:
    """Validate manifest text. ``source`` names the text in messages."""
    issues: list[ConfigIssue] = []
    loader = _Yaml12Loader(text)
    try:
        root = loader.get_single_node()
        if root is not None:
            issues += _check_duplicate_keys(root)
            issues += _check_secrets(root)
            issues += _check_docker_sandbox_alias(root)
        data: object = (
            loader.construct_document(root)  # pyright: ignore[reportUnknownMemberType]
            if root is not None
            else None
        )
    except yaml.MarkedYAMLError as exc:
        raise ConfigValidationError(source, [_yaml_syntax_issue(exc)]) from None
    except yaml.YAMLError as exc:
        issue = ConfigIssue(severity=IssueSeverity.ERROR, path="", message=str(exc))
        raise ConfigValidationError(source, [issue]) from None
    finally:
        loader.dispose()

    if root is None:
        issue = ConfigIssue(
            severity=IssueSeverity.ERROR,
            path="",
            message="the file is empty; a manifest needs at least version, project and migration",
        )
        raise ConfigValidationError(source, [issue])
    if not isinstance(root, MappingNode):
        issue = _issue_at(root.start_mark, "", "the top level must be a mapping of sections")
        raise ConfigValidationError(source, [issue])

    # A credential key is also an unknown key; report each position once.
    flagged = {(i.line, i.column) for i in issues}
    try:
        config = MigrationConfig.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            issue = _schema_issue(root, error)
            if (issue.line, issue.column) not in flagged:
                issues.append(issue)
        raise ConfigValidationError(source, _sorted(issues)) from None

    issues = _sorted(issues)
    if any(i.severity is IssueSeverity.ERROR for i in issues):
        raise ConfigValidationError(source, issues)
    return LoadedConfig(
        source=source, config=config, warnings=issues, config_hash=config_hash(config)
    )


def load_yaml12(text: str, source: str = "<string>") -> object:
    """Parse a YAML document with the manifest's YAML 1.2 scalar rules (rule files use this).

    Raises :class:`ConfigValidationError` with the position of a syntax error.
    """
    try:
        return yaml.load(text, Loader=_Yaml12Loader)  # noqa: S506 - a SafeLoader subclass
    except yaml.MarkedYAMLError as exc:
        raise ConfigValidationError(source, [_yaml_syntax_issue(exc)]) from None
    except yaml.YAMLError as exc:
        issue = ConfigIssue(severity=IssueSeverity.ERROR, path="", message=str(exc))
        raise ConfigValidationError(source, [issue]) from None


def config_hash(config: MigrationConfig) -> str:
    """SHA-256 of the canonical JSON of the effective config; key order and comments don't count."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- YAML-level checks


def _issue_at(
    mark: Mark | None,
    path: str,
    message: str,
    severity: IssueSeverity = IssueSeverity.ERROR,
) -> ConfigIssue:
    return ConfigIssue(
        severity=severity,
        path=path,
        message=message,
        line=mark.line + 1 if mark else None,
        column=mark.column + 1 if mark else None,
    )


def _yaml_syntax_issue(exc: yaml.MarkedYAMLError) -> ConfigIssue:
    mark = exc.problem_mark or exc.context_mark
    problem = exc.problem or "invalid YAML"
    if exc.context:
        problem = f"{exc.context}: {problem}"
    return _issue_at(mark, "", f"YAML syntax error: {problem}")


def _walk(node: Node, loc: Loc, seen: set[int]) -> list[tuple[Loc, Node, Node | None]]:
    """Every (location, value node, key node) below ``node``, aliases visited once."""
    if id(node) in seen:
        return []
    seen.add(id(node))
    found: list[tuple[Loc, Node, Node | None]] = []
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            key: str | int = key_node.value if isinstance(key_node, ScalarNode) else "?"
            child_loc = (*loc, key)
            found.append((child_loc, value_node, key_node))
            found += _walk(value_node, child_loc, seen)
    elif isinstance(node, SequenceNode):
        for index, item in enumerate(node.value):
            child_loc = (*loc, index)
            found.append((child_loc, item, None))
            found += _walk(item, child_loc, seen)
    return found


def _check_duplicate_keys(root: Node) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for loc, node, _ in [((), root, None), *_walk(root, (), set())]:
        if not isinstance(node, MappingNode):
            continue
        first_seen: dict[str, Node] = {}
        for key_node, _value in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.value == "<<":
                continue
            key = key_node.value
            if key in first_seen:
                first_line = first_seen[key].start_mark.line + 1
                issues.append(
                    _issue_at(
                        key_node.start_mark,
                        format_loc((*loc, key)),
                        f"duplicate key `{key}` (first defined on line {first_line})",
                    )
                )
            else:
                first_seen[key] = key_node
    return issues


def _check_secrets(root: Node) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for loc, node, key_node in _walk(root, (), set()):
        path = format_loc(loc)
        if key_node is not None and isinstance(loc[-1], str) and _SECRET_KEY.match(loc[-1]):
            issues.append(
                _issue_at(
                    key_node.start_mark,
                    path,
                    "credentials never go in the manifest (FR-1.6); set them in the "
                    "environment instead (for example ANTHROPIC_API_KEY)",
                )
            )
        elif isinstance(node, ScalarNode) and looks_like_secret(str(node.value)):
            # Never echo the value: the point is to keep it out of logs and terminals.
            issues.append(
                _issue_at(
                    node.start_mark,
                    path,
                    "this value looks like a credential; credentials never go in the manifest "
                    "(FR-1.6). Remove it and rotate the credential if the file was shared",
                )
            )
    return issues


def _check_docker_sandbox_alias(root: Node) -> list[ConfigIssue]:
    """``guardrails.docker_sandbox`` is deprecated, and must agree with ``sandbox.mode``."""
    guardrails, _ = _child(root, "guardrails")
    flag_node, flag_key = _child(guardrails, "docker_sandbox") if guardrails else (None, None)
    if not isinstance(flag_node, ScalarNode) or flag_node.tag != _BOOL_TAG or flag_key is None:
        return []  # absent, or not a boolean (the schema pass reports that)
    implied = SandboxMode.DOCKER if flag_node.value.lower() == "true" else SandboxMode.NATIVE
    path = "guardrails.docker_sandbox"
    sandbox, _ = _child(root, "sandbox")
    mode_node, _ = _child(sandbox, "mode") if sandbox else (None, None)
    if isinstance(mode_node, ScalarNode) and mode_node.value != implied.value:
        message = (
            f"conflicts with sandbox.mode: {mode_node.value}; "
            "remove this deprecated key and keep sandbox.mode"
        )
        return [_issue_at(flag_key.start_mark, path, message)]
    message = f"deprecated; write `sandbox: {{mode: {implied.value}}}` instead"
    return [_issue_at(flag_key.start_mark, path, message, IssueSeverity.WARNING)]


# --------------------------------------------------------------------------- schema errors


def format_loc(loc: Loc) -> str:
    """``("llm", "roles", "editor")`` -> ``llm.roles.editor``; indexes become ``[i]``."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out


def _child(node: Node, part: str | int) -> tuple[Node | None, Node | None]:
    """(value node, key node) for ``part`` inside ``node``, or (None, None)."""
    if isinstance(node, MappingNode) and isinstance(part, str):
        for key_node, value_node in reversed(node.value):  # the last duplicate wins
            if isinstance(key_node, ScalarNode) and key_node.value == part:
                return value_node, key_node
    if isinstance(node, SequenceNode) and isinstance(part, int) and 0 <= part < len(node.value):
        return node.value[part], None
    return None, None


def _child_at(root: Node, loc: Loc) -> tuple[Node | None, Node | None]:
    """The node at ``loc`` if every part of it exists in the file, else (None, None)."""
    node: Node | None = root
    key_node: Node | None = None
    for part in loc:
        if node is None:
            return None, None
        node, key_node = _child(node, part)
    return node, key_node


def _locate(root: Node, loc: Loc) -> tuple[str, Mark | None, Mark | None]:
    """Resolve a pydantic location against the YAML tree.

    Returns the user-facing key path, and the marks of the deepest resolved entry's key and
    value. Location parts that don't exist in the file (union member tags, the key of a missing
    field) are kept in the path only when they are the last part.
    """
    node = root
    shown: list[str | int] = []
    key_mark: Mark | None = None
    value_mark: Mark | None = root.start_mark
    for index, part in enumerate(loc):
        if part == "[key]":
            continue
        child, key_node = _child(node, part)
        if child is None:
            if index == len(loc) - 1:
                shown.append(part)
            continue
        shown.append(part)
        node = child
        key_mark = key_node.start_mark if key_node is not None else child.start_mark
        value_mark = child.start_mark
    return format_loc(tuple(shown)), key_mark, value_mark


def _schema_issue(root: Node, error: ErrorDetails) -> ConfigIssue:
    loc: Loc = tuple(error["loc"])
    path, key_mark, value_mark = _locate(root, loc)
    kind = error["type"]
    message = error["msg"]
    # A problem with a whole section reads best at the section's key, not its first child.
    target, _ = _child_at(root, loc)
    is_section = isinstance(target, MappingNode | SequenceNode)
    mark = (key_mark or value_mark) if is_section else value_mark

    if kind == "missing":
        message = f"required key `{loc[-1]}` is missing"
        path = format_loc(loc[:-1])
        mark = key_mark if loc[:-1] else None
    elif kind == "extra_forbidden":
        key = str(loc[-1])
        path = format_loc(loc[:-1])
        mark = key_mark
        message = f"unknown key `{key}`"
        if key in _UNKNOWN_KEY_HINTS:
            message += f": {_UNKNOWN_KEY_HINTS[key]}"
        else:
            close = difflib.get_close_matches(key, _known_keys(loc[:-1]), n=1)
            if close:
                message += f" (did you mean `{close[0]}`?)"
    elif kind in ("value_error", "assertion_error"):
        message = message.removeprefix("Value error, ").removeprefix("Assertion failed, ")
    elif kind == "string_pattern_mismatch":
        pattern = str(error.get("ctx", {}).get("pattern", ""))
        message = PATTERN_DESCRIPTIONS.get(pattern, message)
    elif "[key]" in loc:
        mark = key_mark
        message = f"invalid key: {message}"

    return ConfigIssue(
        severity=IssueSeverity.ERROR,
        path=path,
        message=message,
        line=mark.line + 1 if mark else None,
        column=mark.column + 1 if mark else None,
    )


def _sorted(issues: list[ConfigIssue]) -> list[ConfigIssue]:
    return sorted(issues, key=lambda i: (i.line or 0, i.column or 0, i.path))


def locate_issues(text: str, found: list[tuple[Loc, str, IssueSeverity]]) -> list[ConfigIssue]:
    """Issues found after loading (e.g. against the installed adapters and rules), positioned
    in the manifest ``text`` by key path: at the value for a scalar, else at the key."""
    loader = _Yaml12Loader(text)
    try:
        root = loader.get_single_node()
    except yaml.YAMLError:
        root = None
    finally:
        loader.dispose()
    issues: list[ConfigIssue] = []
    for loc, message, severity in found:
        mark: Mark | None = None
        path = format_loc(loc)
        if root is not None:
            path, key_mark, value_mark = _locate(root, loc)
            node, _ = _child_at(root, loc)
            mark = value_mark if isinstance(node, ScalarNode) else (key_mark or value_mark)
        issues.append(_issue_at(mark, path, message, severity))
    return _sorted(issues)


# --------------------------------------------------------------------------- key suggestions


def _unwrap(annotation: Any) -> Any:
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            members = [a for a in get_args(annotation) if a is not type(None)]
            if len(members) != 1:
                return annotation
            annotation = members[0]
        else:
            return annotation


def _known_keys(loc: Loc) -> list[str]:
    """Field names (and aliases) of the model at ``loc``, for "did you mean" suggestions."""
    current: Any = MigrationConfig
    for part in loc:
        current = _unwrap(current)
        if isinstance(current, type) and issubclass(current, BaseModel):
            field = current.model_fields.get(str(part))
            if field is None:
                return []
            current = field.annotation
        elif get_origin(current) is dict:
            current = get_args(current)[1]
        elif get_origin(current) is list:
            current = get_args(current)[0]
        else:
            return []
    current = _unwrap(current)
    if not (isinstance(current, type) and issubclass(current, BaseModel)):
        return []
    keys: list[str] = []
    for name, field in current.model_fields.items():
        keys.append(name)
        alias = field.validation_alias
        if alias is not None and not isinstance(alias, str):
            keys += [c for c in getattr(alias, "choices", []) if isinstance(c, str)]
    return keys
