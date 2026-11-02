"""Credential detection and redaction (NFR-5.3, FR-1.6).

One set of patterns serves two purposes:

- :class:`Redactor` scrubs credentials from text before it reaches a log, transcript or report.
- :func:`looks_like_secret` lets the config loader refuse manifests that contain credentials.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

REDACTED = "[REDACTED]"

# Values that are credentials no matter where they appear.
TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),  # Anthropic API and admin keys
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),  # OpenAI-style keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),  # Google API keys
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),  # Hugging Face tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)

# Credentials whose surrounding text must be kept; group 1 is kept, the rest is replaced.
_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(\bbasic\s+)[A-Za-z0-9+/=]{8,}"),
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+(?=@)"),  # user:password@host
)

# `name = value` / `name: value` where the name says the value is a secret.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*?(?:api[_-]?key|secret|passw(?:or)?d|token|credential|private[_-]?key)s?
     ["']?\s*[:=]\s*["']?)
    ([^\s"',;}\]]+)
    """
)

# Environment variables whose values are always redacted, plus any matching this pattern.
_SECRET_ENV_NAME = re.compile(
    r"(?i)(?:^|_)(?:api_?key|token|secret|password|passwd|credentials?|private_key)$"
)
_KNOWN_SECRET_ENV = frozenset(
    {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"}
)
_MIN_LITERAL_LEN = 8


def looks_like_secret(value: str) -> bool:
    """Return True if ``value`` contains something shaped like a credential."""
    return any(p.search(value) for p in TOKEN_PATTERNS)


def is_secret_env_name(name: str) -> bool:
    return name.upper() in _KNOWN_SECRET_ENV or _SECRET_ENV_NAME.search(name) is not None


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group(2)
    # Token counts and similar numbers ("max_tokens: 4096") are not secrets.
    if len(value) < _MIN_LITERAL_LEN or value.isdigit() or value.startswith("["):
        return match.group(0)
    return match.group(1) + REDACTED


@dataclass(frozen=True)
class Redactor:
    """Replaces credentials in text with ``[REDACTED]``.

    ``literals`` are exact secret values (for example the current ``ANTHROPIC_API_KEY``), which
    catches credentials that no pattern recognizes.
    """

    literals: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> Redactor:
        env = os.environ if env is None else env
        return cls.with_literals(v for k, v in env.items() if is_secret_env_name(k))

    @classmethod
    def with_literals(cls, values: Iterable[str]) -> Redactor:
        unique = {v for v in values if len(v) >= _MIN_LITERAL_LEN}
        # Longest first, so a secret that contains another is replaced whole.
        return cls(tuple(sorted(unique, key=len, reverse=True)))

    def redact(self, text: str) -> str:
        if not text:
            return text
        for literal in self.literals:
            text = text.replace(literal, REDACTED)
        for pattern in TOKEN_PATTERNS:
            text = pattern.sub(REDACTED, text)
        for pattern in _CONTEXT_PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        return _ASSIGNMENT.sub(_redact_assignment, text)
