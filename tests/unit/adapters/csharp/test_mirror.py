"""The mirror dotnet builds in (D-65)."""

from __future__ import annotations

import json
from pathlib import Path

from codecoremorph.adapters.csharp import mirror


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_sync_copies_the_worktree_and_keeps_outputs(tmp_path: Path) -> None:
    worktree, copy = tmp_path / "wt", tmp_path / "mirror"
    _write(worktree, {"src/A/A.csproj": "<Project />", "src/A/A.cs": "a", "docs/bin/x.txt": "x"})
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    assert mirror.sync(worktree, copy) == ["docs/bin/x.txt", "src/A/A.cs", "src/A/A.csproj"]
    _write(copy, {"src/A/bin/Debug/A.dll": "dll", "src/A/obj/project.assets.json": "{}"})
    (worktree / "src/A/A.cs").write_text("b", encoding="utf-8")
    (worktree / "docs/bin/x.txt").unlink()
    assert mirror.sync(worktree, copy) == ["docs/bin/x.txt", "src/A/A.cs"]
    assert (copy / "src/A/bin/Debug/A.dll").is_file()  # beside a project: an output
    assert (copy / "src/A/obj/project.assets.json").is_file()
    assert not (copy / ".git").exists()
    assert mirror.sync(worktree, copy) == []
    assert sorted(p.name for p in mirror.output_dirs(copy)) == ["bin", "obj"]


def test_the_mirror_s_global_json_loses_its_sdk_pin(tmp_path: Path) -> None:
    worktree, copy = tmp_path / "wt", tmp_path / "mirror"
    pinned = {"sdk": {"version": "6.0.100", "rollForward": "latestPatch"}, "msbuild-sdks": {}}
    _write(worktree, {"global.json": json.dumps(pinned), "A.csproj": "<Project />"})
    mirror.sync(worktree, copy)
    assert json.loads((copy / "global.json").read_text(encoding="utf-8")) == {"msbuild-sdks": {}}
    assert mirror.sync(worktree, copy) == []  # what it mirrors is stable
    assert mirror.mirrored("global.json", b"not json") == b"not json"
    assert mirror.mirrored("src/x.json", b'{"sdk": 1}') == b'{"sdk": 1}'
