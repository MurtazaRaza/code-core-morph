"""The local web dashboard (FR-12, SPEC §7.13): a FastAPI backend over the store and the run
artifacts, guarded by a per-launch token, serving the built ``dashboard-ui`` app.

- ``data``: the reads (read-only store) and the two writes (gate decisions, abort requests)
- ``app``: the JSON API, the event stream (SSE) and the web UI's files
- ``security``: the Host allowlist, the token-for-cookie exchange and the response headers
- ``server``: binding a port and running uvicorn
"""
