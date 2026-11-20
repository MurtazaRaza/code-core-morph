"""rust-analyzer's expansion of each site of a procedural macro (D-93), through its language
server: the ``rust-analyzer/expandMacro`` request its editors use for "Expand macro
recursively". Runs inside the sandbox with the standard library alone, offline: rust-analyzer
builds the workspace's macro packages (``cargo check``) and expands each site with its own
proc-macro server, on the pinned stable toolchain.

    python expand_macros.py ROOT SITES.json OUT.json [LOG] [TIMEOUT_S]

SITES.json lists ``{"file", "line", "column"}`` (``file`` relative to ROOT; line and column
1-based, as SCIP gives them). OUT.json maps ``"file:line:column"`` to ``{"name",
"expansion"}``, or to null where rust-analyzer gave none; its ``"_server"`` entry says how long
loading and expanding took. LOG gets rust-analyzer's own log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, cast

# rust-analyzer's settings: build the macro packages and expand with them; no checks on save,
# no cache priming, no file watching (nothing changes while it runs).
SETTINGS: dict[str, Any] = {
    "cargo": {"buildScripts": {"enable": True}},
    "procMacro": {"enable": True},
    "checkOnSave": False,
    "cachePriming": {"enable": False},
    "files": {"watcher": "client"},
}
RETRIES = 5


class Server:
    """A language-server session over the child's standard streams."""

    def __init__(self, root: Path, log: IO[bytes]) -> None:
        self.proc = subprocess.Popen(
            ["rust-analyzer"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        self.next_id = 0
        self.status: dict[str, Any] = {}

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        body = json.dumps(message).encode("utf-8")
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def receive(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        length = None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"rust-analyzer exited (status {self.proc.poll()})")
            line = line.strip()
            if not line:
                break
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        if length is None:
            raise RuntimeError("a message from rust-analyzer without a Content-Length")
        return cast(dict[str, Any], json.loads(self.proc.stdout.read(length)))

    def handle(self, message: dict[str, Any]) -> None:
        """The server's own requests get an empty answer, its settings ours; its status is
        kept."""
        method = message.get("method")
        if "id" in message and method is not None:
            result: Any = None
            if method == "workspace/configuration":
                items = cast(list[Any], message.get("params", {}).get("items", []))
                result = [SETTINGS for _ in items]
            self.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        elif method == "experimental/serverStatus":
            self.status = cast(dict[str, Any], message.get("params") or {})

    def request(self, method: str, params: Any) -> dict[str, Any]:
        self.next_id += 1
        mine = self.next_id
        self.send({"jsonrpc": "2.0", "id": mine, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == mine and "method" not in message:
                return message
            self.handle(message)

    def notify(self, method: str, params: Any) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    sites = cast(list[dict[str, Any]], json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    out_path = Path(argv[2])
    log_path = Path(argv[3]) if len(argv) > 3 else Path(os.devnull)
    deadline = time.monotonic() + (float(argv[4]) if len(argv) > 4 else 600.0)
    started = time.monotonic()
    with log_path.open("wb") as log:
        server = Server(root, log)
        try:
            server.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": root.as_uri(),
                    "capabilities": {
                        "experimental": {"serverStatusNotification": True},
                        "general": {"positionEncodings": ["utf-8", "utf-16"]},
                        "window": {"workDoneProgress": False},
                    },
                    "initializationOptions": SETTINGS,
                },
            )
            server.notify("initialized", {})
            while not server.status.get("quiescent"):
                if time.monotonic() > deadline:
                    raise RuntimeError("rust-analyzer didn't finish loading the workspace")
                server.handle(server.receive())
            loaded = time.monotonic() - started
            results: dict[str, Any] = {}
            for site in sites:
                key = f"{site['file']}:{site['line']}:{site['column']}"
                position = {"line": int(site["line"]) - 1, "character": int(site["column"]) - 1}
                params = {
                    "textDocument": {"uri": (root / str(site["file"])).as_uri()},
                    "position": position,
                }
                found: Any = None
                for attempt in range(RETRIES):
                    reply = server.request("rust-analyzer/expandMacro", params)
                    found = reply.get("result")
                    if found or time.monotonic() > deadline:
                        break
                    time.sleep(0.5 * (attempt + 1))  # a macro package still loading
                results[key] = found or None
            results["_server"] = {
                "loaded_s": round(loaded, 1),
                "expanded_s": round(time.monotonic() - started - loaded, 1),
                "health": server.status.get("health"),
                "message": server.status.get("message"),
            }
            out_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
            server.request("shutdown", None)
            server.notify("exit", None)
            server.proc.wait(timeout=30)
        finally:
            if server.proc.poll() is None:
                server.proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
