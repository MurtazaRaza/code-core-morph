"""Serving the dashboard: ports, the launch link, and a live run answered from the dashboard
while its event stream shows the progress (FR-12.1, NFR-1.5, the Phase 7 exit criterion)."""

from __future__ import annotations

import http.client
import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
import uvicorn

from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import RunStatus
from codecoremorph.dashboard import server as server_module
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.server import DEFAULT_PORT, Launch, bind, serve
from codecoremorph.orchestrator.run import RunOptions, RunResult

from .conftest import TOKEN, EngineWorld, fix_macro, live_dashboard, until


def test_bind_takes_the_first_free_port() -> None:
    first = bind("127.0.0.1", None)
    second = bind("127.0.0.1", None)
    try:
        a, b = first.getsockname()[1], second.getsockname()[1]
        assert DEFAULT_PORT <= a < b < DEFAULT_PORT + 20
        with pytest.raises(UsageError, match=f"port {a}"):
            bind("127.0.0.1", a)
    finally:
        first.close()
        second.close()
    with pytest.raises(UsageError, match="can't listen"):
        bind("no-such-host.invalid", 0)


def test_serve_prints_the_launch_link(
    engine_world: EngineWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_world.manifest()
    ctx = DashboardContext.for_config(engine_world.repo / "migration_config.yaml")
    ran: list[uvicorn.Server] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: ran.append(self))
    launches: list[Launch] = []
    serve(ctx, host="127.0.0.1", port=0, open_browser=False, dev=True, on_ready=launches.append)
    (launch,) = launches
    assert launch.url.startswith(f"http://127.0.0.1:{launch.port}/?token=")
    token = launch.url.split("token=", 1)[1]
    assert len(token) >= 32
    assert launch.dev_url == f"http://localhost:5173/api/session/start?token={token}"
    assert len(ran) == 1


def test_shown_host_for_the_link() -> None:
    shown = server_module._shown_host  # pyright: ignore[reportPrivateUsage]
    assert shown("localhost") == "127.0.0.1"
    assert shown("0.0.0.0") == "127.0.0.1"  # noqa: S104
    assert shown("192.168.1.20") == "192.168.1.20"
    assert shown("fe80::1") == "[fe80::1]"


# --------------------------------------------------------------------------- live


def _request(port: int, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
        payload = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        assert response.status == 200, data
        return json.loads(data)
    finally:
        connection.close()


@pytest.mark.slow
def test_a_live_run_is_approved_from_the_dashboard(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(plan="required")
    ctx = DashboardContext.for_config(config)
    outcome: dict[str, RunResult] = {}

    def engine() -> None:
        # No terminal: at the plan gate the engine waits for a decision in the store.
        result, _, _ = engine_world.run(config, [fix_macro()], options=RunOptions(poll_s=0.05))
        outcome["result"] = result

    with live_dashboard(ctx) as port:
        runner = threading.Thread(target=engine, daemon=True)
        runner.start()

        def waiting_run() -> str | None:
            runs = _request(port, "GET", "/api/runs")
            return runs[0]["id"] if runs and runs[0]["waiting"] == ["plan"] else None

        run_id = until(waiting_run, "the run never reached its plan gate")
        detail = _request(port, "GET", f"/api/runs/{run_id}")
        assert detail["engine_alive"] is True
        cursor = detail["last_event_id"]

        received: list[tuple[dict[str, Any], datetime]] = []
        stream_open = threading.Event()

        def watch() -> None:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            connection.request(
                "GET",
                f"/api/runs/{run_id}/events?after={cursor}",
                headers={"authorization": f"Bearer {TOKEN}"},
            )
            response = connection.getresponse()
            stream_open.set()
            try:
                while True:
                    line = response.readline()
                    if not line:
                        return
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        received.append((event, datetime.now(UTC)))
                        if event["type"] == "run.status" and event["data"]["status"] in (
                            "completed",
                            "failed",
                        ):
                            return
            finally:
                connection.close()

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        assert stream_open.wait(10)

        answer = _request(
            port, "POST", f"/api/runs/{run_id}/plan/decision", {"decision": "approve"}
        )
        assert answer["engine_alive"] is True
        assert answer["message"] == "Recorded. The run acts on it right away."

        runner.join(60)
        assert not runner.is_alive()
        watcher.join(10)
        assert outcome["result"].status is RunStatus.COMPLETED

    events = [event for event, _ in received]
    decided = next(e for e in events if e["type"] == "gate.decided")
    assert decided["data"] == {"gate": "plan", "decision": "approve", "source": "dashboard"}
    committed = [
        e for e in events if e["type"] == "unit.status" and e["data"]["status"] == "committed"
    ]
    assert [e["unit_id"] for e in committed] == ["U-0001"]
    # NFR-1.5: each event reached the dashboard within 2 s of the engine writing it.
    lags = [(seen - datetime.fromisoformat(e["ts"])).total_seconds() for e, seen in received]
    assert max(lags) < 2.0, lags
