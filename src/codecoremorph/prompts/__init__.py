"""Versioned prompt templates (CLAUDE.md rule 8, SPEC §7.6).

Templates are Markdown files in ``v<N>/`` next to this module, with ``${name}`` placeholders
(``string.Template``), so JSON and C++ braces in a template need no escaping. A change to any
template's wording is a new version directory: reports and transcripts record the version
(NFR-6.1), and cassettes recorded against one version never silently match another.

Templates hold no timestamps, run IDs or paths, so system prompts stay byte-stable and cache
well. The exception is the host agent's context document (``host_*``, D-41): it names the run's
edits file, and nothing caches it.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from string import Template

PROMPTS_VERSION = "v1"


class PromptError(KeyError):
    """A template or one of its placeholders is missing."""


@cache
def load(name: str, version: str = PROMPTS_VERSION) -> str:
    """The raw text of ``<version>/<name>.md``, with LF line endings."""
    try:
        resource = resources.files(__name__).joinpath(version, f"{name}.md")
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise PromptError(f"no prompt template {version}/{name}.md") from exc
    return text.replace("\r\n", "\n")


def render(name: str, /, version: str = PROMPTS_VERSION, **values: str) -> str:
    """Fill ``${placeholders}``; a missing value is an error, never left in the prompt."""
    try:
        return Template(load(name, version)).substitute(values)
    except KeyError as exc:
        raise PromptError(f"prompt {version}/{name}.md needs a value for {exc}") from exc
