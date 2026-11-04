"""``codecoremorph dashboard``: the local web UI (FR-12, SPEC §7.13)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from codecoremorph.cli.output import handle_errors, stderr, stdout
from codecoremorph.config import DEFAULT_CONFIG_NAME
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.security import is_loopback
from codecoremorph.dashboard.server import Launch, serve


def _ready(launch: Launch) -> None:
    console = stdout()
    console.print(Text("CodeCoreMorph dashboard", style="bold"))
    console.print(Text(f"  {launch.url}"))
    if launch.dev_url:
        console.print(Text(f"  through the Vite dev server: {launch.dev_url}"))
    console.print(
        Text(
            "The link holds this launch's access token: share it only with people who may "
            "approve and abort runs. Press Ctrl-C to stop.",
            style="dim",
        )
    )
    if not launch.ui_built and not launch.dev_url:
        stderr().print(
            Text(
                "warning: the web UI isn't built; the page explains how to build it "
                "(cd dashboard-ui; npm ci; npm run build).",
                style="yellow",
            )
        )
    sys.stdout.flush()  # the server runs until Ctrl-C: piped output must show the link now


@handle_errors
def dashboard_command(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Migration manifest.", show_default=True)
    ] = Path(DEFAULT_CONFIG_NAME),
    port: Annotated[
        int | None,
        typer.Option("--port", "-p", help="Port to listen on (default: the first free from 8765)."),
    ] = None,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Interface to listen on. Anything but 127.0.0.1 lets other machines reach it.",
        ),
    ] = "127.0.0.1",
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open a browser.")] = False,
    dev: Annotated[
        bool,
        typer.Option("--dev", hidden=True, help="Accept the Vite dev server (dashboard-ui)."),
    ] = False,
) -> None:
    """Watch runs live, review plans and diffs, and answer gates in a browser (FR-12)."""
    if not is_loopback(host):
        stderr().print(
            Text(
                f"warning: listening on {host}: other machines on this network can reach the "
                "dashboard, and anyone with its link can approve, reject and abort runs.",
                style="yellow",
            )
        )
    ctx = DashboardContext.for_config(config)
    serve(ctx, host=host, port=port, open_browser=not no_browser, dev=dev, on_ready=_ready)
