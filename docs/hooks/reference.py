"""MkDocs hook: the configuration and CLI reference pages, generated at build time.

The configuration reference comes from the manifest's JSON Schema (``codecoremorph schema``),
the CLI reference from the Typer application, so neither can drift from the code.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from typing import Any

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files

Schema = dict[str, Any]

CONFIG_PAGE = "reference/configuration.md"
CLI_PAGE = "reference/cli.md"


def on_files(files: Files, config: MkDocsConfig) -> Files:
    files.append(File.generated(config, CONFIG_PAGE, content=configuration_page()))
    files.append(File.generated(config, CLI_PAGE, content=cli_page()))
    return files


# --------------------------------------------------------------------------- configuration


def _resolve(node: Schema, defs: Schema) -> Schema:
    ref = node.get("$ref")
    if isinstance(ref, str):
        return {**defs[ref.rsplit("/", 1)[-1]], **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def _type(node: Schema, defs: Schema) -> str:
    node = _resolve(node, defs)
    if "enum" in node:
        return " \\| ".join(f"`{json.dumps(v)}`" for v in node["enum"])
    if "const" in node:
        return f"`{json.dumps(node['const'])}`"
    if "anyOf" in node:
        options = [_type(option, defs) for option in node["anyOf"]]
        return " or ".join(dict.fromkeys(options))
    kind = node.get("type")
    if kind == "array":
        return f"list of {_type(node.get('items', {}), defs)}"
    if kind == "object":
        values = node.get("additionalProperties") or next(
            iter(node.get("patternProperties", {}).values()), None
        )
        if isinstance(values, dict) and values:
            return f"map of name to {_type(values, defs)}"
        return "section"
    return {"null": "null", "integer": "integer", "number": "number"}.get(kind or "", kind or "any")


def _limits(node: Schema) -> str:
    parts = []
    for key, sign in (("minimum", ">="), ("exclusiveMinimum", ">"), ("minLength", "length >=")):
        if key in node:
            parts.append(f"{sign} {node[key]}")
    for option in node.get("anyOf", []):
        parts += [p for p in [_limits(option)] if p]
    return "; ".join(dict.fromkeys(parts))


def _section(node: Schema, defs: Schema) -> Schema | None:
    """The object with properties that ``node`` is, or may be (an optional section)."""
    if "properties" in node:
        return node
    for option in node.get("anyOf", []):
        resolved = _resolve(option, defs)
        if "properties" in resolved:
            return resolved
    return None


def _default(node: Schema, required: bool) -> str:
    if "default" in node:
        value = node["default"]
        if isinstance(value, dict) and len(json.dumps(value)) > 40:
            return "see the description"
        return f"`{json.dumps(value)}`"
    if required:
        return "required"
    # A default from a factory doesn't appear in the schema: an empty list or map.
    if node.get("type") == "array":
        return "`[]`"
    if node.get("type") == "object" and "properties" not in node:
        return "`{}`"
    return ""


def _rows(node: Schema, defs: Schema, path: str, required: bool) -> Iterator[list[str]]:
    node = _resolve(node, defs)
    description = node.get("description", "")
    if node.get("deprecated"):
        description = f"**Deprecated.** {description}"
    limits = _limits(node)
    if limits:
        description = f"{description} ({limits})" if description else f"({limits})"
    names = _resolve(node.get("propertyNames", {}), defs)
    key = "<name>"
    extra: list[str] = []
    if "enum" in names:
        key = f"<{str(names.get('title', 'name')).lower()}>"
        extra.append(f"Keys: {', '.join(f'`{v}`' for v in names['enum'])}.")
    if isinstance(node.get("default"), dict) and len(json.dumps(node["default"])) > 40:
        extra.append(f"Default: `{json.dumps(node['default'])}`.")
    if extra:
        description = " ".join([description.rstrip(".") + "." if description else "", *extra])
    yield [f"`{path}`", _type(node, defs), _default(node, required), description.strip()]
    section = _section(node, defs)
    if section is not None:
        needed = set(section.get("required", []))
        for name, child in section["properties"].items():
            yield from _rows(child, defs, f"{path}.{name}", name in needed)
    values = node.get("additionalProperties") or next(
        iter(node.get("patternProperties", {}).values()), None
    )
    if isinstance(values, dict) and values:
        resolved = _resolve(values, defs)
        if "properties" in resolved:
            needed = set(resolved.get("required", []))
            for name, child in resolved["properties"].items():
                yield from _rows(child, defs, f"{path}.{key}.{name}", name in needed)


def configuration_page() -> str:
    from codecoremorph.config import SCHEMA_VERSION, manifest_json_schema

    schema = manifest_json_schema()
    defs: Schema = schema.get("$defs", {})
    needed = set(schema.get("required", []))
    lines = [
        "# Configuration reference",
        "",
        f"Every key of `migration_config.yaml`, manifest version {SCHEMA_VERSION}. This page "
        "is generated from the manifest's JSON Schema, which `codecoremorph schema` prints for "
        "editor completion. `codecoremorph init` writes a commented starter, and "
        "`codecoremorph validate` checks a manifest and reports every problem with its line "
        "and column.",
        "",
        "Unknown keys are errors. The manifest is read as YAML 1.2, so only `true` and `false` "
        "are booleans, and credentials are refused: they come from the environment or the "
        "provider SDK's credential chain.",
        "",
    ]
    for name, node in schema["properties"].items():
        lines += [f"## `{name}`", ""]
        resolved = _resolve(node, defs)
        if resolved.get("description"):
            lines += [resolved["description"].rstrip(".") + ".", ""]
        lines += ["| Key | Type | Default | Description |", "|---|---|---|---|"]
        for row in _rows(node, defs, name, name in needed):
            lines.append("| " + " | ".join(cell.replace("\n", " ") for cell in row) + " |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
# Typer bundles its own copy of Click, so commands and parameters are read by their attributes.


def _is_group(command: Any) -> bool:
    return isinstance(getattr(command, "commands", None), dict)


def _is_option(param: Any) -> bool:
    return getattr(param, "param_type_name", "") == "option" and not getattr(param, "hidden", False)


def _is_argument(param: Any) -> bool:
    return getattr(param, "param_type_name", "") == "argument"


def _commands(group: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    for name, command in group.commands.items():
        if getattr(command, "hidden", False):
            continue
        path = f"{prefix} {name}"
        yield path, command
        if _is_group(command):
            yield from _commands(command, path)


def _usage(path: str, command: Any) -> str:
    pieces = [path]
    if any(_is_option(p) for p in command.params):
        pieces.append("[OPTIONS]")
    for param in command.params:
        if _is_argument(param):
            name = (param.metavar or param.name or "ARG").upper()
            pieces.append(name if param.required else f"[{name}]")
    if _is_group(command):
        pieces.append("COMMAND")
    return " ".join(pieces)


def cli_page() -> str:
    from typer.main import get_command

    from codecoremorph.cli.app import app

    root = get_command(app)
    lines = [
        "# CLI reference",
        "",
        "Every `codecoremorph` command and option. This page is generated from the CLI; "
        "`codecoremorph COMMAND --help` prints the same.",
        "",
        "Global options, before the command: `--verbose` (`-v`) shows debug logging, "
        "`--log-file PATH` also writes a JSON-lines debug log, `--version` prints the version.",
        "",
    ]
    for path, command in _commands(root, "codecoremorph"):
        lines += [f"## `{path}`", ""]
        help_text = inspect.cleandoc(command.help or "").strip()
        if help_text:
            lines += [help_text, ""]
        lines += ["```text", _usage(path, command), "```", ""]
        rows = []
        for param in command.params:
            if _is_option(param):
                names = ", ".join(f"`{o}`" for o in [*param.opts, *param.secondary_opts])
                text = (param.help or "").replace("\n", " ")
                if (
                    not param.is_flag
                    and param.default not in (None, (), False)
                    and not callable(param.default)
                ):
                    text += f" Default: `{param.default}`."
                rows.append(f"| {names} | {text.strip()} |")
            elif _is_argument(param):
                name = (param.metavar or param.name or "ARG").upper()
                text = getattr(param, "help", "") or ""
                rows.append(f"| `{name}` | {text.strip()} |")
        if rows:
            lines += ["| Argument or option | Description |", "|---|---|", *rows, ""]
    return "\n".join(lines)
