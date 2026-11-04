## What and why

<!-- What the change does, and the problem it solves. Link the issue or the plan task. -->

## How it was checked

- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright`
- [ ] `uv run pytest`
- [ ] `uv run pytest -m toolchain` or `-m docker`, if adapters, the sandbox or runs changed
- [ ] `npm run typecheck` and `npm test` in `dashboard-ui/`, if the UI changed

## Checklist

- [ ] New behavior has tests; a bug fix has a regression test
- [ ] SPEC.md is updated (with a `D-xx` row) where the implementation differs from it
- [ ] `.bat` and `.sh` scripts changed together, if either did
- [ ] Nothing in `fixtures/` was modernized or fixed
