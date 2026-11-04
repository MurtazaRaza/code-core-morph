"""The dashboard's access guard (FR-12.7, SPEC §10).

- **Host allowlist.** A request whose ``Host`` header isn't one of the dashboard's own
  addresses is refused, which defeats DNS rebinding.
- **Per-launch token.** ``codecoremorph dashboard`` prints a URL with a random token. The first
  request carrying it gets an ``HttpOnly``, ``SameSite=Strict`` cookie and a redirect to the
  same address without the token; every later request needs the cookie (tools may send
  ``Authorization: Bearer <token>`` instead).
- **Same-origin writes.** A POST must come from the dashboard's own origin; there is no CORS.
- **Security headers** on every response: no framing, no sniffing, no referrer, and a content
  security policy that allows only the dashboard's own scripts and connections.

It is a plain ASGI middleware, so the event stream passes through unbuffered.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import socket
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

TOKEN_PARAM = "token"  # noqa: S105 - the query parameter's name, not a secret
# The token exchange through the Vite dev server's proxy, which forwards only /api.
SESSION_START = "/api/session/start"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK = ("127.0.0.1", "localhost", "::1")
# The Vite dev server's addresses (`codecoremorph dashboard --dev`).
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",  # the diff editor and the graph style inline
        "img-src 'self' data:",
        "font-src 'self' data:",
        "worker-src 'self' blob:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_loopback(host: str) -> bool:
    return host in LOOPBACK


def _url_host(address: str) -> str:
    """``address`` as it appears in a URL or a Host header: IPv6 in brackets."""
    try:
        if ipaddress.ip_address(address).version == 6:
            return f"[{address}]"
    except ValueError:
        pass
    return address


def _names_for(host: str) -> set[str]:
    """The names a browser may use to reach the dashboard bound to ``host``."""
    if host not in ("0.0.0.0", "::"):  # noqa: S104 - matching the user's --host, not binding
        return {_url_host(host)}
    names = {socket.gethostname(), socket.getfqdn()}
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        infos = []
    addresses = {str(info[4][0]).split("%", 1)[0] for info in infos}
    return {_url_host(n) for n in names | addresses if n}


@dataclass(frozen=True)
class Access:
    """Who may use one dashboard launch: its token, Host names and origins."""

    token: str
    port: int
    hosts: frozenset[str]  # Host header values, lower case, with the port
    origins: frozenset[str]  # Origin header values allowed to write

    @classmethod
    def for_bind(
        cls,
        host: str,
        port: int,
        *,
        token: str | None = None,
        extra_origins: Iterable[str] = (),
    ) -> Access:
        names = {"127.0.0.1", "localhost", "[::1]"}
        if not is_loopback(host):
            names |= _names_for(host)
        hosts = frozenset(f"{name}:{port}".lower() for name in names)
        origins = {f"http://{h}" for h in hosts} | {o.rstrip("/").lower() for o in extra_origins}
        return cls(token=token or new_token(), port=port, hosts=hosts, origins=frozenset(origins))

    @property
    def cookie_name(self) -> str:
        # Browsers share cookies across ports: each launch's port keeps its own cookie.
        return f"codecoremorph_dashboard_{self.port}"

    def matches(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self.token.encode("utf-8"))


def _headers(scope: Scope) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = bytes(raw_name).decode("latin-1").lower()
        value = bytes(raw_value).decode("latin-1")
        found[name] = f"{found[name]}, {value}" if name in found else value
    return found


async def _respond(
    send: Send,
    status: int,
    body: bytes,
    content_type: bytes,
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
        *SECURITY_HEADERS,
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


_DENIED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>CodeCoreMorph dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto;
padding: 0 1rem; line-height: 1.5">
<h1>Open the dashboard with its link</h1>
<p>{message}</p>
<p>Run <code>codecoremorph dashboard</code> and open the address it prints: it holds this
launch's access token. Each launch has a new token, so an older link doesn't work.</p>
</body></html>
"""


class Guard:
    """ASGI middleware enforcing :class:`Access` on every HTTP request."""

    def __init__(self, app: ASGIApp, access: Access):
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        path = str(scope.get("path", "/"))
        api = path.startswith("/api/")
        if headers.get("host", "").lower() not in self.access.hosts:
            await self._refuse(send, 400, "this address isn't the dashboard's", api=api)
            return
        method = str(scope.get("method", "GET")).upper()
        params = parse_qsl(bytes(scope.get("query_string", b"")).decode("latin-1"))
        token = next((value for key, value in params if key == TOKEN_PARAM), None)
        if token is not None and method == "GET":
            if not self.access.matches(token):
                await self._refuse(send, 401, "the link's token is not this launch's", api=api)
                return
            rest = urlencode([(k, v) for k, v in params if k != TOKEN_PARAM])
            location = "/" if path == SESSION_START else path + (f"?{rest}" if rest else "")
            cookie = (
                f"{self.access.cookie_name}={self.access.token}; HttpOnly; SameSite=Strict; Path=/"
            )
            await _respond(
                send,
                303,
                b"",
                b"text/plain",
                [(b"location", location.encode("latin-1")), (b"set-cookie", cookie.encode())],
            )
            return
        bearer = self._bearer(headers)
        if not (bearer or self._cookie(headers)):
            await self._refuse(send, 401, "this browser hasn't opened this launch's link", api=api)
            return
        if method in UNSAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None and origin.rstrip("/").lower() not in self.access.origins:
                await self._refuse(send, 403, "the request came from another site", api=api)
                return
            if origin is None and not bearer:
                await self._refuse(send, 403, "a browser's write must name its origin", api=api)
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _bearer(self, headers: dict[str, str]) -> bool:
        value = headers.get("authorization", "")
        scheme, _, token = value.partition(" ")
        return scheme.lower() == "bearer" and bool(token) and self.access.matches(token.strip())

    def _cookie(self, headers: dict[str, str]) -> bool:
        raw = headers.get("cookie")
        if not raw:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            return False
        morsel = jar.get(self.access.cookie_name)
        return morsel is not None and self.access.matches(morsel.value)

    async def _refuse(self, send: Send, status: int, message: str, *, api: bool) -> None:
        if api:
            body = json.dumps(
                {
                    "error": message,
                    "hint": "Open the dashboard with the link `codecoremorph dashboard` printed.",
                }
            ).encode("utf-8")
            await _respond(send, status, body, b"application/json")
            return
        page = _DENIED_PAGE.format(message=message[:1].upper() + message[1:] + ".")
        await _respond(send, status, page.encode("utf-8"), b"text/html; charset=utf-8")


__all__ = [
    "DEV_ORIGINS",
    "SESSION_START",
    "TOKEN_PARAM",
    "Access",
    "Guard",
    "is_loopback",
    "new_token",
]
