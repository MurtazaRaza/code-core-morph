from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codecoremorph.core.secrets import (
    REDACTED,
    Redactor,
    is_secret_env_name,
    looks_like_secret,
)

ANTHROPIC_KEY = "sk-ant-api03-" + "A1b2C3d4" * 6
# Assembled at runtime so the literal never sits in the source as a scannable token.
GITHUB_TOKEN = "ghp" + "_" + "x" * 36
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.mark.parametrize(
    "secret",
    [
        ANTHROPIC_KEY,
        "sk-proj-" + "a" * 40,
        GITHUB_TOKEN,
        "github_pat_" + "b" * 30,
        AWS_KEY,
        "AIza" + "c" * 35,
        "xoxb-" + "1234567890-abc",
        "hf_" + "d" * 34,
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_known_token_shapes_are_redacted(secret: str) -> None:
    text = f"calling api with {secret} now"
    redacted = Redactor().redact(text)
    assert secret not in redacted
    assert REDACTED in redacted
    assert looks_like_secret(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Bearer abcdefgh12345678", "Authorization: Bearer [REDACTED]"),
        ("auth basic dXNlcjpwYXNzd29yZA==", "auth basic [REDACTED]"),
        ("https://user:hunter2hunter2@example.com/x", "https://user:[REDACTED]@example.com/x"),
        ("api_key=abcdefgh1234", "api_key=[REDACTED]"),
        ('{"client_secret": "s3cr3t-value-x"}', '{"client_secret": "[REDACTED]"}'),
        ("password: correcthorsebattery", "password: [REDACTED]"),
    ],
)
def test_contextual_secrets_keep_their_labels(text: str, expected: str) -> None:
    assert Redactor().redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "max_run_tokens: null",
        "input_tokens=123456789",
        "cache_read_tokens: 4096",
        "token=short",
        "the tokenizer is fine",
        "nothing to see here",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert Redactor().redact(text) == text
    assert not looks_like_secret(text)


def test_literals_from_environment() -> None:
    env = {"MY_SERVICE_TOKEN": "opaque-value-123", "PATH": "/usr/bin", "SHORT_TOKEN": "abc"}
    redactor = Redactor.from_environment(env)
    assert redactor.redact("value opaque-value-123 here") == f"value {REDACTED} here"
    assert redactor.redact("/usr/bin") == "/usr/bin"
    assert redactor.literals == ("opaque-value-123",)


def test_longest_literal_wins() -> None:
    redactor = Redactor.with_literals(["abcdefgh", "abcdefgh-longer"])
    assert redactor.redact("x abcdefgh-longer y") == f"x {REDACTED} y"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ANTHROPIC_API_KEY", True),
        ("ANTHROPIC_AUTH_TOKEN", True),
        ("GH_TOKEN", True),
        ("DB_PASSWORD", True),
        ("AWS_SECRET", True),
        ("PATH", False),
        ("TOKENIZERS_PARALLELISM", False),
        ("HOME", False),
    ],
)
def test_secret_env_names(name: str, expected: bool) -> None:
    assert is_secret_env_name(name) is expected


@given(st.text(max_size=200))
def test_redaction_is_idempotent(text: str) -> None:
    redactor = Redactor.with_literals(["literal-secret"])
    once = redactor.redact(text)
    assert redactor.redact(once) == once


@given(st.text(max_size=80), st.text(max_size=80))
def test_embedded_key_never_survives(prefix: str, suffix: str) -> None:
    redacted = Redactor().redact(f"{prefix} {ANTHROPIC_KEY} {suffix}")
    assert ANTHROPIC_KEY not in redacted


def test_json_keeps_its_shape_and_numbers() -> None:
    data = {
        "reason": f"the build said {ANTHROPIC_KEY}",
        "usage": {"input_tokens": 4096, "cost_usd": 1.25, "cached": True},
        "lines": [f"token={GITHUB_TOKEN}", None, 3],
    }
    assert Redactor().redact_json(data) == {
        "reason": f"the build said {REDACTED}",
        "usage": {"input_tokens": 4096, "cost_usd": 1.25, "cached": True},
        "lines": [f"token={REDACTED}", None, 3],
    }
