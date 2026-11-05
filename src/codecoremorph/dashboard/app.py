"""The dashboard's FastAPI application (SPEC §7.13): the JSON API under ``/api``, the event
stream, and the built web UI (``dashboard-ui``) for every other path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from importlib import resources
from pathlib import Path

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from codecoremorph.core.errors import (
    CodeCoreMorphError,
    NotFoundError,
    RunNotActiveError,
    StoreError,
    UsageError,
)
from codecoremorph.core.models import Event, RunReport
from codecoremorph.dashboard import data
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.models import (
    AbortBody,
    ActionResult,
    AttemptView,
    DecisionBody,
    ErrorBody,
    GraphView,
    PlanView,
    PortView,
    RunDetail,
    RunSummary,
    SessionInfo,
    TextFile,
    UnitView,
)
from codecoremorph.dashboard.security import Access, Guard

POLL_S = 0.5  # NFR-1.5: the dashboard shows an event within 2 s
KEEPALIVE_S = 15.0
EVENT_BATCH = 500
SOURCE_CHECKOUT_DIST = Path(__file__).resolve().parents[3] / "dashboard-ui" / "dist"

_NO_UI_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>CodeCoreMorph dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto;
padding: 0 1rem; line-height: 1.5">
<h1>The dashboard's web UI isn't built</h1>
<p>This CodeCoreMorph runs from a source checkout without the built UI. Build it with
<code>cd dashboard-ui; npm ci; npm run build</code> and restart
<code>codecoremorph dashboard</code>, or run <code>npm run dev</code> there with
<code>codecoremorph dashboard --dev</code>. The API under <code>/api</code> works meanwhile.</p>
</body></html>
"""


def find_static() -> Path | None:
    """The built web UI: the package's copy (wheels), else a source checkout's build."""
    packaged = Path(str(resources.files("codecoremorph.dashboard") / "static"))
    for candidate in (packaged, SOURCE_CHECKOUT_DIST):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _status(exc: CodeCoreMorphError) -> int:
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, RunNotActiveError | UsageError):
        return 409
    if isinstance(exc, StoreError):
        return 503
    return 500


def _error(exc: CodeCoreMorphError) -> JSONResponse:
    body = ErrorBody(error=exc.message, reason=exc.reason, hint=exc.hint)
    return JSONResponse(body.model_dump(mode="json"), status_code=_status(exc))


def _sse(event: Event) -> str:
    payload = event.model_dump_json()
    return f"id: {event.id}\ndata: {payload}\n\n"


def api_router(ctx: DashboardContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/session")
    def session() -> SessionInfo:
        return data.session_info(ctx)

    @router.get("/session/start")
    def session_start() -> SessionInfo:  # the Guard handles the token; this is its landing
        return data.session_info(ctx)

    @router.get("/runs")
    def runs() -> list[RunSummary]:
        return data.list_runs(ctx)

    @router.get("/runs/{run_id}")
    def run(run_id: str) -> RunDetail:
        return data.run_detail(ctx, run_id)

    @router.get("/runs/{run_id}/plan")
    def plan(run_id: str) -> PlanView:
        return data.plan_view(ctx, run_id)

    @router.post("/runs/{run_id}/plan/decision")
    def plan_decision(run_id: str, body: DecisionBody) -> ActionResult:
        return data.decide_plan(ctx, run_id, body)

    @router.get("/runs/{run_id}/graph")
    def graph(run_id: str) -> GraphView:
        return data.graph_view(ctx, run_id)

    @router.get("/runs/{run_id}/units/{unit_id}")
    def unit(run_id: str, unit_id: str) -> UnitView:
        return data.unit_view(ctx, run_id, unit_id)

    @router.get("/runs/{run_id}/units/{unit_id}/port")
    def port(run_id: str, unit_id: str) -> PortView:
        return data.port_view(ctx, run_id, unit_id)

    @router.get("/runs/{run_id}/units/{unit_id}/attempts/{number}")
    def attempt(run_id: str, unit_id: str, number: int) -> AttemptView:
        return data.attempt_view(ctx, run_id, unit_id, number)

    @router.get("/runs/{run_id}/units/{unit_id}/attempts/{number}/files/{name}")
    def attempt_file(run_id: str, unit_id: str, number: int, name: str) -> TextFile:
        return data.attempt_file(ctx, run_id, unit_id, number, name)

    @router.post("/runs/{run_id}/units/{unit_id}/decision")
    def unit_decision(run_id: str, unit_id: str, body: DecisionBody) -> ActionResult:
        return data.decide_unit(ctx, run_id, unit_id, body)

    @router.get("/runs/{run_id}/report", response_model=None)
    def report(run_id: str, format: str = "json", download: bool = False) -> Response:  # noqa: A002
        if format not in ("json", "md"):
            raise UsageError("format must be json or md")
        if format == "md":
            body = data.report_markdown(ctx, run_id)
            media = "text/markdown; charset=utf-8"
        else:
            built: RunReport = data.report(ctx, run_id)
            body = built.model_dump_json(indent=2)
            media = "application/json"
        headers = {"cache-control": "no-store"}
        if download:
            headers["content-disposition"] = f'attachment; filename="report-{run_id}.{format}"'
        return Response(body, media_type=media, headers=headers)

    @router.post("/runs/{run_id}/abort")
    def abort(run_id: str, body: AbortBody) -> ActionResult:
        return data.abort(ctx, run_id, body)

    @router.get("/runs/{run_id}/events", response_model=None)
    async def events(
        request: Request, run_id: str, after: int | None = None, follow: bool = True
    ) -> StreamingResponse:
        """Server-sent events: the run's events after ``after`` or ``Last-Event-ID``, whichever
        is later (a browser reconnects with the URL's ``after`` and the last ID it saw), then
        each new one as the engine writes it. ``follow=false`` sends what exists and ends."""
        run_id = await run_in_threadpool(data.require_run, ctx, run_id)
        cursor = max(after or 0, _last_event_id(request))

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            yield "retry: 2000\n\n"
            idle = 0.0
            while True:
                batch = await run_in_threadpool(data.events_after, ctx, run_id, cursor, EVENT_BATCH)
                for event in batch:
                    yield _sse(event)
                    cursor = event.id or cursor
                if len(batch) == EVENT_BATCH:
                    continue
                if not follow or request.app.state.should_stop():
                    return  # ending the stream lets a Ctrl-C shut the server down at once
                if await request.is_disconnected():
                    return
                idle = 0.0 if batch else idle + POLL_S
                if idle >= KEEPALIVE_S:
                    yield ": keep-alive\n\n"
                    idle = 0.0
                await anyio.sleep(POLL_S)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    return router


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id", "0")
    return int(raw) if raw.isdigit() else 0


def create_app(ctx: DashboardContext, access: Access, *, static_dir: Path | None = None) -> FastAPI:
    """The dashboard app, guarded by ``access``. ``static_dir`` holds the built web UI. The
    server sets ``app.state.should_stop`` so open event streams end when it shuts down."""
    app = FastAPI(title="CodeCoreMorph dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.should_stop = lambda: False
    app.include_router(api_router(ctx))

    @app.exception_handler(CodeCoreMorphError)
    async def domain_error(_request: Request, exc: CodeCoreMorphError) -> JSONResponse:
        return _error(exc)

    if static_dir is not None and (static_dir / "index.html").is_file():
        index = static_dir / "index.html"
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        # Files at the top of the build (favicon and the like), by name only: nothing else
        # under the static directory is reachable.
        top = {p.name: p for p in static_dir.iterdir() if p.is_file() and p.name != "index.html"}

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        def spa(path: str) -> Response:
            if path.startswith("api/"):
                return _error(NotFoundError(f"there is no API endpoint /{path}"))
            if path in top:
                return FileResponse(top[path])
            return FileResponse(index, headers={"cache-control": "no-store"})

    else:

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        def no_ui(path: str) -> Response:
            if path.startswith("api/"):
                return _error(NotFoundError(f"there is no API endpoint /{path}"))
            return HTMLResponse(_NO_UI_PAGE, headers={"cache-control": "no-store"})

    app.add_middleware(Guard, access=access)
    return app


__all__ = ["create_app", "find_static"]
