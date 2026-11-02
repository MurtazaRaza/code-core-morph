"""Validation issues with their position in the manifest file."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from codecoremorph.core.errors import ConfigError


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ConfigIssue(BaseModel):
    """One problem in a manifest, located by key path and (when known) line and column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: IssueSeverity
    path: str = Field(description='Key path such as "llm.roles.editor" or "rules[1]"; "" = root')
    message: str
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)

    def render(self, source: str) -> str:
        """Compiler-style text: ``file:line:col: error: key.path: message``."""
        location = source
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        where = f"{self.path}: " if self.path else ""
        return f"{location}: {self.severity.value}: {where}{self.message}"


class ConfigValidationError(ConfigError):
    """The manifest has one or more errors. ``issues`` holds all of them, plus any warnings."""

    def __init__(self, source: str, issues: list[ConfigIssue]):
        errors = sum(1 for i in issues if i.severity is IssueSeverity.ERROR)
        noun = "error" if errors == 1 else "errors"
        super().__init__(
            f"{source} has {errors} {noun}",
            hint="Fix the issues listed above, then run `codecoremorph validate` again.",
        )
        self.source = source
        self.issues = issues

    def render_issues(self) -> list[str]:
        return [issue.render(self.source) for issue in self.issues]
