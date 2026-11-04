"""Repositories with runs for the dashboard's tests: the run engine with the fake adapter and a
scripted model (no toolchain), served by the dashboard app through a test client."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from codecoremorph.core.models import CompletionResult, RunStatus
from codecoremorph.dashboard.app import create_app
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.security import Access
from codecoremorph.dashboard.server import bind

from ..agent.conftest import replace_edit, reply, submit
from ..orchestrator.conftest import FIXED, MACRO, EngineWorld, GitRunner, make_engine_world

PORT = 8765
TOKEN = "dashboard-test-token-0123456789"
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = BASE  # what a browser sends with the dashboard's own writes


@pytest.fixture
def engine_world(tmp_path: Path, git: GitRunner) -> EngineWorld:
    return make_engine_world(tmp_path, git)


def fix_macro() -> CompletionResult:
    return reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))


def access(port: int = PORT) -> Access:
    return Access.for_bind("127.0.0.1", port, token=TOKEN)


def client_for(
    ctx: DashboardContext, *, static_dir: Path | None = None, login: bool = True
) -> TestClient:
    """A browser-like client: it opened the launch's link, so it holds the cookie."""
    client = TestClient(create_app(ctx, access(), static_dir=static_dir), base_url=BASE)
    if login:
        response = client.get(f"/?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303, response.text
    return client


def until(check: Callable[[], Any], message: str, timeout_s: float = 15.0) -> Any:
    """Poll ``check`` until it returns something truthy, and return that."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(message)


@contextmanager
def live_dashboard(ctx: DashboardContext, *, static_dir: Path | None = None) -> Iterator[int]:
    """The dashboard on a real socket, in a thread; yields its port."""
    sock = bind("127.0.0.1", 0)
    port = int(sock.getsockname()[1])
    app = create_app(ctx, Access.for_bind("127.0.0.1", port, token=TOKEN), static_dir=static_dir)
    config = uvicorn.Config(
        app, log_config=None, lifespan="off", log_level="warning", timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    app.state.should_stop = lambda: server.should_exit
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    until(lambda: server.started, "the server didn't start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def sse_events(text: str) -> list[dict[str, Any]]:
    """The events of a finished event stream, with their SSE ids."""
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in lines:
            event = json.loads(lines["data"])
            assert str(event["id"]) == lines["id"]
            events.append(event)
    return events


@dataclass
class Dashboard:
    world: EngineWorld
    ctx: DashboardContext
    client: TestClient

    @property
    def config(self) -> Path:
        return self.world.repo / "migration_config.yaml"

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.post(path, json=body, headers={"origin": ORIGIN})


def dashboard_for(world: EngineWorld) -> Dashboard:
    ctx = DashboardContext.for_config(world.repo / "migration_config.yaml")
    return Dashboard(world, ctx, client_for(ctx))


@pytest.fixture
def completed(engine_world: EngineWorld) -> tuple[Dashboard, str]:
    """A run that committed its one unit and finished."""
    config = engine_world.manifest()
    result, _, _ = engine_world.run(config, [fix_macro()])
    assert result.status is RunStatus.COMPLETED, result.reason
    return dashboard_for(engine_world), result.run_id


@pytest.fixture
def parked_at_plan(engine_world: EngineWorld) -> tuple[Dashboard, str]:
    """A run that waits at the plan gate; no engine is running it."""
    config = engine_world.manifest(plan="required")
    result, _, _ = engine_world.run(
        config, [], options=engine_world.options(decide=lambda _r: None)
    )
    assert result.parked
    return dashboard_for(engine_world), result.run_id
