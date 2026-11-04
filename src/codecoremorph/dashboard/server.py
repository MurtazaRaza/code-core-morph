"""``codecoremorph dashboard``: bind a local port, serve the app with uvicorn, open the browser
(FR-12, SPEC §7.13)."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

import uvicorn

from codecoremorph.core.errors import UsageError
from codecoremorph.dashboard.app import create_app, find_static
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.security import (
    DEV_ORIGINS,
    SESSION_START,
    Access,
    is_loopback,
)

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
PORT_TRIES = 20
SHUTDOWN_GRACE_S = 2  # open event streams don't hold Ctrl-C up


@dataclass(frozen=True)
class Launch:
    url: str  # the link with this launch's token
    dev_url: str | None  # the same through the Vite dev server (--dev)
    host: str
    port: int
    ui_built: bool


def bind(host: str, port: int | None) -> socket.socket:
    """A listening socket on ``host``: ``port``, or the first free one from 8765."""
    ports = [port] if port is not None else list(range(DEFAULT_PORT, DEFAULT_PORT + PORT_TRIES))
    last: OSError | None = None
    for candidate in ports:
        try:
            infos = socket.getaddrinfo(
                host, candidate, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
            )
        except socket.gaierror as exc:
            raise UsageError(
                f"can't listen on {host!r}",
                reason=str(exc),
                hint="Give --host an IP address of this machine, or leave it out.",
            ) from exc
        family, kind, proto, _, address = infos[0]
        sock = socket.socket(family, kind, proto)
        try:
            if sys.platform == "win32":
                # Without it, Windows lets another program bind the same port.
                sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0), 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
            sock.listen(128)
        except OSError as exc:
            sock.close()
            last = exc
            continue
        return sock
    tried = str(port) if port is not None else f"{ports[0]}-{ports[-1]}"
    raise UsageError(
        f"can't listen on {host} port {tried}",
        reason=str(last) if last else None,
        hint="Another program (or another dashboard) uses it: pass a free port with --port.",
    )


def _shown_host(host: str) -> str:
    if is_loopback(host) or host in ("0.0.0.0", "::"):  # noqa: S104 - naming, not binding
        return "127.0.0.1"
    return f"[{host}]" if ":" in host else host


def _open_when_started(server: uvicorn.Server, url: str) -> None:
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)


def serve(
    ctx: DashboardContext,
    *,
    host: str,
    port: int | None,
    open_browser: bool,
    dev: bool,
    on_ready: Callable[[Launch], None],
) -> None:
    """Serve until Ctrl-C. ``on_ready`` gets the launch's link before the server starts."""
    sock = bind(host, port)
    actual = int(sock.getsockname()[1])
    access = Access.for_bind(host, actual, extra_origins=DEV_ORIGINS if dev else ())
    static = find_static()
    app = create_app(ctx, access, static_dir=static)
    url = f"http://{_shown_host(host)}:{actual}/?token={access.token}"
    dev_url = f"{DEV_ORIGINS[0]}{SESSION_START}?token={access.token}" if dev else None
    config = uvicorn.Config(
        app,
        log_config=None,  # keep CodeCoreMorph's logging
        log_level="warning",
        access_log=False,
        lifespan="off",
        server_header=False,
        timeout_graceful_shutdown=SHUTDOWN_GRACE_S,
    )
    server = uvicorn.Server(config)
    app.state.should_stop = lambda: server.should_exit
    on_ready(Launch(url=url, dev_url=dev_url, host=host, port=actual, ui_built=static is not None))
    if open_browser:
        threading.Thread(
            target=_open_when_started, args=(server, dev_url or url), daemon=True
        ).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


__all__ = ["DEFAULT_PORT", "Launch", "bind", "serve"]
