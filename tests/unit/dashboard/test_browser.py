"""The dashboard in a real browser: the Phase 7 smoke test (start a run, approve its plan in
the UI, see its unit commit) and the exit criterion "the plan gate and a unit gate can be
approved from the dashboard, and progress updates live".

Needs the built UI (``cd dashboard-ui; npm ci; npm run build``) and a Chromium-based browser:
the installed Chrome on Windows and macOS (no download), Playwright's Chromium elsewhere
(``uv run playwright install chromium``); ``CODECOREMORPH_TEST_BROWSER`` picks another channel
(``msedge``, ``chromium``)."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

from codecoremorph.core.models import RunStatus
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.orchestrator.run import RunOptions, RunResult

from .conftest import TOKEN, EngineWorld, fix_macro, live_dashboard

pytestmark = [pytest.mark.browser, pytest.mark.slow]

DIST = Path(__file__).resolve().parents[3] / "dashboard-ui" / "dist"
CHANNEL = os.environ.get(
    "CODECOREMORPH_TEST_BROWSER", "chromium" if sys.platform.startswith("linux") else "chrome"
)
TIMEOUT_MS = 20_000


@pytest.fixture
def page() -> Iterator[Page]:
    if not (DIST / "index.html").is_file():
        pytest.fail("build the dashboard UI first: cd dashboard-ui; npm ci; npm run build")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel=None if CHANNEL == "chromium" else CHANNEL)
        except PlaywrightError as exc:
            pytest.fail(f"can't start the {CHANNEL} browser: {exc}")
        try:
            yield browser.new_page(viewport={"width": 1360, "height": 900})
        finally:
            browser.close()


def test_approve_both_gates_in_the_browser_and_watch_the_run_finish(
    engine_world: EngineWorld, page: Page, tmp_path: Path
) -> None:
    config = engine_world.manifest(plan="required", unit="all")
    ctx = DashboardContext.for_config(config)
    problems: list[str] = []
    page.on(
        "console",
        lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    outcome: dict[str, RunResult] = {}

    def engine() -> None:
        result, _, _ = engine_world.run(config, [fix_macro()], options=RunOptions(poll_s=0.05))
        outcome["result"] = result

    runner = threading.Thread(target=engine, daemon=True)
    try:
        with live_dashboard(ctx, static_dir=DIST) as port:
            runner.start()
            _drive(page, f"http://127.0.0.1:{port}", runner, outcome, tmp_path)
    except BaseException:
        page.screenshot(path=tmp_path / "failure.png", full_page=True)
        (tmp_path / "failure.html").write_text(page.content(), encoding="utf-8")
        raise
    finally:
        if runner.is_alive():  # don't leave the engine waiting at its gate
            _abort(engine_world)
            runner.join(30)
    assert problems == []


def _abort(world: EngineWorld) -> None:
    from datetime import UTC, datetime

    from codecoremorph.core.models import ApprovalDecision, ApprovalGate, ApprovalRecord

    with world.store() as store:
        for run in store.list_runs():
            store.add_approval(
                ApprovalRecord(
                    run_id=run.id,
                    gate=ApprovalGate.RUN,
                    decision=ApprovalDecision.ABORT,
                    source="test",
                    created_at=datetime.now(UTC),
                )
            )


def _drive(
    page: Page,
    base: str,
    runner: threading.Thread,
    outcome: dict[str, RunResult],
    tmp_path: Path,
) -> None:
    page.goto(f"{base}/?token={TOKEN}")
    expect(page).to_have_url(f"{base}/")  # the token went into a cookie
    page.get_by_role("link", name="plan approval").click(timeout=TIMEOUT_MS)
    expect(page.get_by_role("heading", name="Plan gate")).to_be_visible()
    run_id = page.url.split("/runs/", 1)[1].split("/", 1)[0]
    page.screenshot(path=tmp_path / "plan.png", full_page=True)
    page.get_by_role("button", name="Approve the plan").click()
    expect(page.get_by_role("status").filter(has_text="Recorded.")).to_be_visible()

    # The unit commits and waits at its gate; the overview shows it without a reload.
    page.get_by_role("link", name="Overview").click()
    page.get_by_role("link", name="unit's page").click(timeout=TIMEOUT_MS)
    expect(page.get_by_role("heading", name="Unit gate")).to_be_visible()
    page.screenshot(path=tmp_path / "unit-gate.png", full_page=True)
    page.get_by_role("button", name="Keep the unit").click()
    expect(page.get_by_role("status").filter(has_text="Recorded.")).to_be_visible()

    runner.join(60)
    assert not runner.is_alive()
    assert outcome["result"].status is RunStatus.COMPLETED

    # Live: the unit's page follows it to its end without a reload, and so does the run's.
    expect(page.locator(".page-head .badge")).to_have_text("committed", timeout=TIMEOUT_MS)
    page.locator(".crumbs").get_by_role("link", name=run_id).click()
    expect(page.locator(".page-head .badge")).to_have_text("completed", timeout=TIMEOUT_MS)
    expect(page.get_by_text("1 committed")).to_be_visible()
    page.screenshot(path=tmp_path / "overview.png", full_page=True)

    page.goto(f"{base}/runs/{run_id}/units/U-0001")
    expect(page.locator(".monaco-diff-editor")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.locator(".monaco-diff-editor")).to_contain_text("using N = T")
    page.screenshot(path=tmp_path / "unit.png", full_page=True)

    page.goto(f"{base}/runs/{run_id}/graph")
    expect(page.locator(".graph-canvas canvas").first).to_be_attached(timeout=TIMEOUT_MS)
    page.screenshot(path=tmp_path / "graph.png", full_page=True)

    page.goto(f"{base}/runs/{run_id}/report")
    expect(page.get_by_role("link", name="Download Markdown")).to_be_visible()
