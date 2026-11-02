# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents working inside a compile → test → fix loop.
Real toolchains decide what's correct: every change is built and tested in a sandbox, and
only verified changes are committed, to a dedicated branch you review and merge yourself.

The first target is **C++ standard upgrades**: from C++98/03, 11, 14, 17 or 20 to any newer
standard up to C++23. Java and C# version upgrades come next, then cross-language ports
(Java → C#, C++ → Rust).

> **Status:** early development (Phase 0, foundations). Nothing here migrates code yet.

## Developer setup

You need git ≥ 2.30. Docker is optional for now. No admin rights are required.

```sh
scripts\bootstrap-dev.bat        # Windows (cmd.exe or PowerShell)
./scripts/bootstrap-dev.sh       # Linux, macOS, Git Bash
```

The bootstrap script uses `uv` from your PATH if it's recent enough, or downloads a pinned,
checksum-verified copy into `CODECOREMORPH_HOME`. It then installs Python 3.12 with uv,
syncs the dependencies and installs the pre-commit hooks.

```sh
uv run codecoremorph --version
uv run codecoremorph validate examples/cpp11-to-cpp20.yaml
uv run pytest
```

## Documentation

- [docs/SPEC.md](docs/SPEC.md): product and technical specification
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): phased implementation plan

## License

Apache-2.0. See [LICENSE](LICENSE).
