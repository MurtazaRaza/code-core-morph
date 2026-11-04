# The dashboard

```sh
codecoremorph dashboard -c path/to/migration_config.yaml
```

This opens a local page in your browser, for the repository the manifest describes. In it you
can:

- follow runs live: progress, budget use, the current step, what waits for a decision;
- review the plan and approve it, leaving units out if you like, or reject it;
- explore the dependency graph of units or files, colored by unit status;
- read each unit's attempts: the diff side by side, compiler messages, test regressions,
  invariant results, the feedback the agent got and its transcript;
- keep or roll back a unit waiting at its gate;
- read the report, or download it as Markdown or JSON.

A gate that `codecoremorph run` is asking about in a terminal can be answered from the dashboard
as well; whichever answer comes first wins.

## Access

The dashboard listens on 127.0.0.1 only, on the first free port from 8765 (`--port` picks one).
Its link holds a token that is new at every launch; the browser trades it for a cookie on the
first visit. It accepts only requests addressed to itself (against DNS rebinding), changes made
from its own pages, and no cross-origin requests. Ctrl-C stops it.

`--host` makes it listen on another interface, with a warning: anyone who has the link can then
approve and abort runs.

## From a source checkout

Installed packages include the web UI. In a source checkout, build it once with Node.js (which
`bootstrap-dev` installs): `cd dashboard-ui; npm ci; npm run build`. For work on the UI itself,
run `npm run dev` there and `codecoremorph dashboard --dev`, which accepts the development
server's origin.
