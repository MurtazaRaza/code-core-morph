"""Hatch build hook: the wheel carries the built dashboard UI (SPEC §7.13, NFR-4.3), so users
never need Node. ``dashboard-ui/dist`` (``npm run build``) goes in as
``codecoremorph/dashboard/static``. Editable installs serve it from the checkout instead.

Without a build the wheel has no web UI: a warning, or an error when
``CODECOREMORPH_REQUIRE_DASHBOARD_UI=1`` (release builds)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

UI_DIST = Path("dashboard-ui") / "dist"
PACKAGE_PATH = "codecoremorph/dashboard/static"
REQUIRE = "CODECOREMORPH_REQUIRE_DASHBOARD_UI"


class DashboardAssetsHook(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        dist = Path(self.root) / UI_DIST
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = PACKAGE_PATH
            return
        message = (
            "dashboard-ui/dist is missing, so this wheel has no dashboard web UI. Build it "
            "first: cd dashboard-ui; npm ci; npm run build"
        )
        if os.environ.get(REQUIRE) == "1":
            raise RuntimeError(message)
        self.app.display_warning(message)
