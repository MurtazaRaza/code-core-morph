"""Domain exceptions and process exit codes.

Every user-facing error says what happened, why, and what to do next (NFR-8.1). Raise one of the
classes below with a short ``message`` (what happened), an optional ``reason`` (why) and an
optional ``hint`` (what to do next). The CLI renders all three and exits with ``exit_code``.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes of the ``codecoremorph`` CLI (SPEC §7.14)."""

    SUCCESS = 0
    COMPLETED_WITH_FAILURES = 1
    CONFIG_OR_USAGE = 2
    ENVIRONMENT = 3
    BUDGET_EXHAUSTED = 4
    ABORTED = 5
    REJECTED = 6
    INTERNAL = 10


class CodeCoreMorphError(Exception):
    """Base class of every CodeCoreMorph domain error."""

    exit_code: ExitCode = ExitCode.INTERNAL

    def __init__(self, message: str, *, reason: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.hint = hint

    def __str__(self) -> str:
        parts = [self.message]
        if self.reason:
            parts.append(f"Why: {self.reason}")
        if self.hint:
            parts.append(f"Next: {self.hint}")
        return "\n".join(parts)


class ConfigError(CodeCoreMorphError):
    """The migration manifest is missing, unreadable or invalid."""

    exit_code = ExitCode.CONFIG_OR_USAGE


class UsageError(CodeCoreMorphError):
    """The command line was used incorrectly."""

    exit_code = ExitCode.CONFIG_OR_USAGE


class HostEnvironmentError(CodeCoreMorphError):
    """A required tool or service on the host is missing, too old or unreachable."""

    exit_code = ExitCode.ENVIRONMENT


class StoreError(CodeCoreMorphError):
    """The state database or artifact directory can't be used."""

    exit_code = ExitCode.ENVIRONMENT


class VcsError(CodeCoreMorphError):
    """A git operation failed."""

    exit_code = ExitCode.ENVIRONMENT


class GitCommandError(VcsError):
    """A git command exited with an error. ``argv`` and ``stderr`` say which and why."""

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        returncode: int | None,
        stderr: str = "",
        hint: str | None = None,
    ):
        super().__init__(message, reason=stderr.strip() or None, hint=hint)
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr


class NothingToCommitError(VcsError):
    """A commit was requested, but the worktree has no changes."""


class RepoLockedError(VcsError):
    """Another CodeCoreMorph run holds the repository lock (FR-6.6)."""


class RunNotActiveError(UsageError):
    """A command continues a run that isn't active: it finished, was aborted, or never ran."""


class SafetyViolationError(VcsError):
    """A guard against touching anything but the run worktree tripped (I-11). Always a bug."""

    exit_code = ExitCode.INTERNAL


class SandboxError(HostEnvironmentError):
    """The sandbox (Docker or native) can't run a command as asked."""


class BaselineError(CodeCoreMorphError):
    """The project doesn't configure, build or pass its tests at the base commit (FR-7.1), so
    there is no green starting point to migrate from."""

    exit_code = ExitCode.ENVIRONMENT


class LLMError(CodeCoreMorphError):
    """A model provider call failed (SPEC §7.7 error taxonomy). ``retryable`` errors may
    succeed on a later attempt."""

    exit_code = ExitCode.ENVIRONMENT
    retryable: bool = False


class RateLimitedError(LLMError):
    retryable = True


class ProviderUnavailableError(LLMError):
    """The provider is down, overloaded or unreachable."""

    retryable = True


class LLMTimeoutError(LLMError):
    retryable = True


class ProviderAuthError(LLMError):
    """Credentials are missing, invalid or not allowed to use the model."""


class InvalidLLMRequestError(LLMError):
    """The provider rejected the request itself: usually a bug, or an unknown model."""

    exit_code = ExitCode.INTERNAL


class ContextOverflowError(LLMError):
    """The prompt doesn't fit the model's context window."""


class RefusedError(LLMError):
    """The model declined the request, after any configured fallback."""


class ReplayMissError(LLMError):
    """A replayed test made a request its cassette doesn't hold (FR-8.4). Fails loudly."""

    exit_code = ExitCode.INTERNAL


class BudgetExhaustedError(CodeCoreMorphError):
    """A run's token or cost cap is reached (FR-14.2, NFR-7.1)."""

    exit_code = ExitCode.BUDGET_EXHAUSTED


class InternalError(CodeCoreMorphError):
    """A bug in CodeCoreMorph. Report it with the log file attached."""

    exit_code = ExitCode.INTERNAL
