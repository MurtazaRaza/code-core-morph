"""The dashboard's JSON API and event stream over real runs (FR-12.1-12.5, SPEC §7.13)."""

from __future__ import annotations

from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    RunReport,
    RunStatus,
    UnitStatus,
)

from .conftest import Dashboard, EngineWorld, dashboard_for, fix_macro, sse_events


def test_runs_list_and_overview(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    assert client.get("/api/session").json()["project"] == "geo"
    (summary,) = client.get("/api/runs").json()
    assert summary["id"] == run_id
    assert summary["status"] == "completed"
    assert summary["unit_counts"] == {"committed": 1}
    assert summary["branch"] == f"codecoremorph/{run_id}"
    assert summary["waiting"] == []
    assert summary["engine_alive"] is False
    assert summary["agent"] == "model"

    detail = client.get(f"/api/runs/{run_id}").json()
    assert (detail["units_total"], detail["units_done"]) == (1, 1)
    assert detail["plan_ready"]
    assert detail["graph_ready"]
    assert detail["report_ready"]
    assert detail["activity"]["message"]  # the engine's steps are recorded as events
    assert detail["events"][-1]["id"] == detail["last_event_id"]
    assert detail["budget"]["max_cost_usd"] is not None
    assert detail["agent_waiting"] is None
    assert (detail["source_standard"], detail["target_standard"]) == ("cpp11", "cpp11")


def test_plan_graph_and_unit_views(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    plan = client.get(f"/api/runs/{run_id}/plan").json()
    assert plan["gate_open"] is False
    assert [u["status"] for u in plan["plan"]["units"]] == ["committed"]  # from the store

    graph = client.get(f"/api/runs/{run_id}/graph").json()
    files = {f["path"]: f for f in graph["files"]}
    assert {"include/geo.h", "src/geo.cpp", "tests/geo_test.cpp"} <= set(files)
    assert ["src/geo.cpp", "include/geo.h"] in graph["edges"]
    assert files["include/geo.h"]["units"] == ["U-0001"]
    assert files["include/geo.h"]["status"] == "committed"
    assert files["tests/geo_test.cpp"]["status"] is None  # in no unit
    assert [u["id"] for u in graph["units"]] == ["U-0001"]

    unit = client.get(f"/api/runs/{run_id}/units/u-0001").json()  # IDs are case-insensitive
    assert unit["unit"]["status"] == "committed"
    assert unit["result"]["commit"]
    assert [a["number"] for a in unit["attempts"]] == [1]
    assert unit["gate"]["waiting"] is False
    assert [f["number"] for f in unit["attempt_files"]] == [1]


def test_attempt_view_and_files(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    base = f"/api/runs/{run_id}/units/U-0001/attempts"
    view = client.get(f"{base}/1").json()
    assert view["attempt"]["outcome"] == "succeeded"
    assert view["diff"] is not None
    assert "using N = T" in view["diff"]["text"]
    assert view["transcript"], "the agent's conversation is kept"
    assert {entry["role"] for entry in view["transcript"]} >= {"user", "assistant"}
    name = view["files"][0]
    shown = client.get(f"{base}/1/files/{name}").json()
    assert shown["name"] == name
    assert client.get(f"{base}/1/files/result.json").status_code == 404  # not in the attempt
    assert client.get(f"{base}/1/files/..%2F..%2Fplan.json").status_code == 404
    assert client.get(f"{base}/2").status_code == 404
    assert client.get(f"{base}/0").status_code == 404


def test_report_as_json_and_markdown(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    report = RunReport.model_validate(client.get(f"/api/runs/{run_id}/report").json())
    assert report.status is RunStatus.COMPLETED
    markdown = client.get(f"/api/runs/{run_id}/report", params={"format": "md", "download": True})
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.headers["content-disposition"] == f'attachment; filename="report-{run_id}.md"'
    assert "**Status:** completed" in markdown.text
    assert client.get(f"/api/runs/{run_id}/report", params={"format": "pdf"}).status_code == 409


def test_a_report_for_a_run_in_progress(parked_at_plan: tuple[Dashboard, str]) -> None:
    dash, run_id = parked_at_plan
    report = dash.client.get(f"/api/runs/{run_id}/report").json()
    assert report["status"] == "awaiting_plan_approval"
    assert report["reason"] == "in progress"


def test_the_event_stream(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    response = client.get(f"/api/runs/{run_id}/events", params={"follow": False})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    types = {e["type"] for e in events}
    assert {"step.started", "unit.status", "run.status"} <= types
    after = ids[2]
    later = sse_events(
        client.get(f"/api/runs/{run_id}/events", params={"follow": False, "after": after}).text
    )
    assert [e["id"] for e in later] == ids[3:]
    resumed = sse_events(
        client.get(
            f"/api/runs/{run_id}/events",
            params={"follow": False},
            headers={"last-event-id": str(ids[-2])},
        ).text
    )
    assert [e["id"] for e in resumed] == ids[-1:]
    # A browser reconnects with its URL's `after` and the last ID it saw: the later one wins.
    reconnected = sse_events(
        client.get(
            f"/api/runs/{run_id}/events",
            params={"follow": False, "after": ids[0]},
            headers={"last-event-id": str(ids[-2])},
        ).text
    )
    assert [e["id"] for e in reconnected] == ids[-1:]


def test_missing_things_are_404_with_an_explanation(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    client = dash.client
    missing = client.get("/api/runs/20990101-000000-0000")
    assert missing.status_code == 404
    assert "no run" in missing.json()["error"]
    assert client.get("/api/runs/not-a-run").status_code == 404
    assert client.get(f"/api/runs/{run_id}/units/U-0099").status_code == 404
    assert client.get(f"/api/runs/{run_id}/units/..%2F..").status_code == 404
    assert client.get("/api/runs/20990101-000000-0000/events").status_code == 404
    assert client.get("/api/nothing").status_code == 404


def test_no_runs_yet(engine_world: EngineWorld) -> None:
    engine_world.manifest()
    dash = dashboard_for(engine_world)
    assert dash.client.get("/api/runs").json() == []
    assert not (engine_world.repo / ".codecoremorph").exists()  # the dashboard created nothing


# --------------------------------------------------------------------------- decisions


def test_approve_the_plan_from_the_dashboard(parked_at_plan: tuple[Dashboard, str]) -> None:
    dash, run_id = parked_at_plan
    client = dash.client
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "awaiting_plan_approval"
    assert detail["waiting"] == ["plan"]
    plan = client.get(f"/api/runs/{run_id}/plan").json()
    assert plan["gate_open"] is True
    assert plan["pending"] is None

    answer = dash.post(
        f"/api/runs/{run_id}/plan/decision",
        {"decision": "approve", "exclude": ["u-0001"], "reason": "not this one"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["engine_alive"] is False
    assert f"codecoremorph resume {run_id}" in body["message"]
    with dash.world.store() as store:
        (approval,) = store.approvals(run_id, pending=True)
    assert approval.gate is ApprovalGate.PLAN
    assert approval.decision is ApprovalDecision.APPROVE
    assert approval.source == "dashboard"
    assert approval.exclude == ["U-0001"]
    assert client.get(f"/api/runs/{run_id}/plan").json()["pending"]["id"] == approval.id

    resumed, _, _ = dash.world.run(dash.config, [], run_id=run_id, resume=True)
    assert resumed.status is RunStatus.COMPLETED_WITH_FAILURES  # its one unit was excluded
    with dash.world.store() as store:
        (unit,) = store.units(run_id)
    assert unit.status is UnitStatus.EXCLUDED


def test_plan_decisions_are_checked(parked_at_plan: tuple[Dashboard, str]) -> None:
    dash, run_id = parked_at_plan
    url = f"/api/runs/{run_id}/plan/decision"
    rejected = dash.post(url, {"decision": "reject", "exclude": ["U-0001"]})
    assert rejected.status_code == 409
    assert "approving" in rejected.json()["error"]
    assert dash.post(url, {"decision": "maybe"}).status_code == 422
    assert dash.post(url, {"decision": "approve", "extra": 1}).status_code == 422


def test_a_decided_plan_cant_be_decided_again(completed: tuple[Dashboard, str]) -> None:
    dash, run_id = completed
    answer = dash.post(f"/api/runs/{run_id}/plan/decision", {"decision": "approve"})
    assert answer.status_code == 409
    assert "already decided" in answer.json()["error"]


def test_keep_a_unit_from_the_dashboard(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(unit="all")
    result, _, _ = engine_world.run(
        config, [fix_macro()], options=engine_world.options(decide=lambda _r: None)
    )
    assert result.parked
    dash = dashboard_for(engine_world)
    run_id = result.run_id
    unit = dash.client.get(f"/api/runs/{run_id}/units/U-0001").json()
    assert unit["gate"]["waiting"] is True
    assert unit["gate"]["commit"] == result.head
    assert dash.client.get(f"/api/runs/{run_id}").json()["waiting"] == ["U-0001"]

    wrong = dash.post(f"/api/runs/{run_id}/units/U-0002/decision", {"decision": "approve"})
    assert wrong.status_code == 409
    assert "isn't waiting" in wrong.json()["error"]
    kept = dash.post(f"/api/runs/{run_id}/units/U-0001/decision", {"decision": "approve"})
    assert kept.status_code == 200, kept.text
    with engine_world.store() as store:
        (approval,) = store.approvals(run_id, pending=True)
    assert (approval.gate, approval.unit_id, approval.source) == (
        ApprovalGate.UNIT,
        "U-0001",
        "dashboard",
    )
    resumed, _, _ = engine_world.run(config, [], run_id=run_id, resume=True)
    assert resumed.status is RunStatus.COMPLETED


def test_abort_a_parked_run_from_the_dashboard(parked_at_plan: tuple[Dashboard, str]) -> None:
    dash, run_id = parked_at_plan
    answer = dash.post(f"/api/runs/{run_id}/abort", {"reason": "not today"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["engine_alive"] is False
    assert "report written" in answer.json()["message"]
    detail = dash.client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "aborted"
    assert detail["report_ready"] is True
    with dash.world.store() as store:
        (approval,) = store.approvals(run_id)
    assert approval.source == "dashboard"
    again = dash.post(f"/api/runs/{run_id}/abort", {})
    assert again.status_code == 409
    assert "already ended" in again.json()["error"]
