"""The dashboard's access guard (FR-12.7, SPEC §10) and how it serves the web UI."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codecoremorph.dashboard.app import create_app
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.security import DEV_ORIGINS, Access

from .conftest import BASE, ORIGIN, PORT, TOKEN, EngineWorld, access, client_for


@pytest.fixture
def ctx(engine_world: EngineWorld) -> DashboardContext:
    engine_world.manifest()
    return DashboardContext.for_config(engine_world.repo / "migration_config.yaml")


def _anonymous(ctx: DashboardContext, *, base_url: str = BASE) -> TestClient:
    return TestClient(create_app(ctx, access()), base_url=base_url)


def test_the_token_is_exchanged_for_a_cookie(ctx: DashboardContext) -> None:
    client = _anonymous(ctx)
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"codecoremorph_dashboard_{PORT}={TOKEN};")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert client.get("/api/runs").status_code == 200  # the client kept the cookie


def test_a_deep_link_keeps_its_path_and_other_parameters(ctx: DashboardContext) -> None:
    client = _anonymous(ctx)
    response = client.get(f"/runs/x?tab=plan&token={TOKEN}", follow_redirects=False)
    assert response.headers["location"] == "/runs/x?tab=plan"
    dev = _anonymous(ctx).get(f"/api/session/start?token={TOKEN}", follow_redirects=False)
    assert dev.headers["location"] == "/"  # through the Vite proxy, back to the app


def test_requests_without_the_token_are_refused(ctx: DashboardContext) -> None:
    client = _anonymous(ctx)
    api = client.get("/api/runs")
    assert api.status_code == 401
    assert "link" in api.json()["hint"]
    page = client.get("/")
    assert page.status_code == 401
    assert "Open the dashboard with its link" in page.text
    wrong = client.get("/?token=not-the-token", follow_redirects=False)
    assert wrong.status_code == 401
    assert "set-cookie" not in wrong.headers
    client.cookies.set(f"codecoremorph_dashboard_{PORT}", "guess")
    assert client.get("/api/runs").status_code == 401


def test_another_launchs_cookie_doesnt_open_this_one(ctx: DashboardContext) -> None:
    other = TestClient(create_app(ctx, access(PORT + 1)), base_url=f"http://127.0.0.1:{PORT + 1}")
    assert other.get(f"/?token={TOKEN}", follow_redirects=False).status_code == 303
    # Same token in this test, but the cookie is named for its own port.
    client = _anonymous(ctx)
    client.cookies.set(f"codecoremorph_dashboard_{PORT + 1}", TOKEN)
    assert client.get("/api/runs").status_code == 401


def test_a_foreign_host_header_is_refused(ctx: DashboardContext) -> None:
    rebinding = _anonymous(ctx, base_url=f"http://evil.example:{PORT}")
    assert rebinding.get(f"/?token={TOKEN}", follow_redirects=False).status_code == 400
    assert rebinding.get("/api/runs", headers={"authorization": f"Bearer {TOKEN}"}).status_code == (
        400
    )
    other_port = _anonymous(ctx, base_url=f"http://127.0.0.1:{PORT + 7}")
    assert other_port.get("/api/runs").status_code == 400
    for host in ("localhost", "[::1]"):
        assert (
            _anonymous(ctx, base_url=f"http://{host}:{PORT}")
            .get("/api/runs", headers={"authorization": f"Bearer {TOKEN}"})
            .status_code
            == 200
        )


def test_writes_must_come_from_the_dashboard(ctx: DashboardContext) -> None:
    client = client_for(ctx)
    url = "/api/runs/20990101-000000-0000/abort"
    assert client.post(url, json={}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post(url, json={}).status_code == 403  # a browser always names its origin
    # The dashboard's own origin gets through to the endpoint (no such run here).
    assert client.post(url, json={}, headers={"origin": ORIGIN}).status_code == 404
    # A tool with the token as a bearer credential needs no origin.
    tool = _anonymous(ctx)
    bearer = {"authorization": f"Bearer {TOKEN}"}
    assert tool.post(url, json={}, headers=bearer).status_code == 404
    assert tool.post(url, json={}, headers={"authorization": "Bearer nope"}).status_code == 401


def test_every_response_carries_the_security_headers(ctx: DashboardContext) -> None:
    for response in (
        _anonymous(ctx).get("/api/runs"),
        _anonymous(ctx, base_url=f"http://evil.example:{PORT}").get("/"),
        client_for(ctx).get("/api/runs"),
        client_for(ctx).get("/"),
    ):
        headers = response.headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        csp = headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "access-control-allow-origin" not in headers


def test_allowed_hosts_and_origins() -> None:
    local = Access.for_bind("127.0.0.1", 9000, token="t")
    assert local.hosts == {"127.0.0.1:9000", "localhost:9000", "[::1]:9000"}
    assert "http://localhost:9000" in local.origins
    assert not any(o.endswith(":5173") for o in local.origins)
    dev = Access.for_bind("127.0.0.1", 9000, token="t", extra_origins=DEV_ORIGINS)
    assert set(DEV_ORIGINS) <= dev.origins
    lan = Access.for_bind("0.0.0.0", 9000, token="t")  # noqa: S104 - what --host 0.0.0.0 allows
    assert f"{socket.gethostname().lower()}:9000" in lan.hosts
    assert local.hosts < lan.hosts
    one = Access.for_bind("192.168.1.20", 9000, token="t")
    assert "192.168.1.20:9000" in one.hosts
    v6 = Access.for_bind("fe80::1", 9000, token="t")
    assert "[fe80::1]:9000" in v6.hosts
    assert Access.for_bind("127.0.0.1", 9000).token != Access.for_bind("127.0.0.1", 9000).token


# --------------------------------------------------------------------------- the web UI


def _build(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root>app</div>", encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return dist


def test_the_built_ui_is_served_with_client_side_routes(
    ctx: DashboardContext, tmp_path: Path
) -> None:
    client = client_for(ctx, static_dir=_build(tmp_path))
    for path in ("/", "/runs/20990101-000000-0000/units/U-0001", "/anything"):
        page = client.get(path)
        assert page.status_code == 200
        assert "app" in page.text
        assert page.headers["cache-control"] == "no-store"
    assert client.get("/assets/index-abc.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"
    assert "outside" not in client.get("/../secret.txt").text
    assert "outside" not in client.get("/assets/..%2F..%2Fsecret.txt").text
    missing = client.get("/api/not-an-endpoint")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/json"


def test_without_a_built_ui_the_page_explains_how_to_build_it(ctx: DashboardContext) -> None:
    page = client_for(ctx).get("/")
    assert page.status_code == 200
    assert "isn't built" in page.text
    assert "npm run build" in page.text
