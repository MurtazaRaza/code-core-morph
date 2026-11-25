"""Async code in a port from C# (D-96): the C# index's notices of what the tasks runtime takes
over make a type's unit place the runtime (``PortUnit.tasks``) and its note say what the port
does; C++ ports async code to C++20's coroutines, so a C++17 target refuses the type, by site.
What the runtime doesn't take over is a refusal of the index's, by site, as before."""

from __future__ import annotations

from codecoremorph.core.models import PortNotice, PortNoticeKind, PortRefusal, RefusalSite

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _by_type, _plan


def _relay(p: FakeProject) -> Library:
    """A cache over a store interface, both async, a plain type, and a type that awaits .NET's
    own I/O (refused by the index)."""
    lib = Library(p).project(LIB).project(TESTS, "test", references=[LIB])
    store = lib.type(
        "Relay.IStore", "src/Relay/Store.cs", kind="interface",
        members=["FetchAsync(System.String,System.Threading.CancellationToken)"],
        notices=[PortNotice(what=PortNoticeKind.ASYNC, file="src/Relay/Store.cs", line=10,
                            detail="FetchAsync (a task or a token in its signature)")],
    )  # fmt: skip
    cache = lib.type(
        "Relay.Cache", "src/Relay/Cache.cs", members=["GetAsync(System.String)"],
        notices=[PortNotice(what=PortNoticeKind.ASYNC, file="src/Relay/Cache.cs", line=44,
                            detail="async GetAsync", member="csharp:Relay.Cache.GetAsync")],
    )  # fmt: skip
    lib.uses("src/Relay/Cache.cs", f"{cache}.GetAsync(System.String)", store)
    lib.type("Relay.Key", "src/Relay/Key.cs", members=["Text"])
    lib.type(
        "Relay.Loader", "src/Relay/Loader.cs", members=["LoadAsync(System.String)"],
        refusals=[RefusalSite(what=PortRefusal.ASYNC, file="src/Relay/Loader.cs", line=12,
                              detail="System.IO.StreamReader.ReadToEndAsync returns a task of "
                                     ".NET's own, which the port has no counterpart of")],
    )  # fmt: skip
    return lib


def test_cpp20_ports_async_code_and_its_units_place_the_tasks_runtime(
    fake_project: FakeProject,
) -> None:
    units = _by_type(_plan(_relay(fake_project).load(), "cpp20"))
    cache, store, key = units["Relay.Cache"], units["Relay.IStore"], units["Relay.Key"]
    assert cache.port is not None
    assert cache.port.tasks
    assert cache.port.codemod_options()["tasks"] == "true"
    assert store.port is not None
    assert store.port.tasks
    assert key.port is not None
    assert not key.port.tasks
    assert "tasks" not in key.port.codemod_options()
    note = cache.note or ""
    assert "async code (async GetAsync (src/Relay/Cache.cs:44))" in note
    assert "coroutines returning ccm::tasks::Task (C++20" in note
    assert "D-96" in note
    # What the runtime doesn't take over stays refused, by site.
    loader = units["Relay.Loader"]
    assert loader.port is None
    assert "System.IO.StreamReader.ReadToEndAsync" in (loader.note or "")


def test_cpp17_refuses_async_code_by_site(fake_project: FakeProject) -> None:
    units = _by_type(_plan(_relay(fake_project).load(), "cpp17"))
    cache = units["Relay.Cache"]
    assert cache.port is None
    assert (cache.note or "").startswith("skipped: Relay.Cache has async code (async GetAsync ")
    assert "set migration.target.standard to cpp20 or cpp23 (D-96)" in (cache.note or "")
    assert units["Relay.IStore"].port is None
    key = units["Relay.Key"]
    assert key.port is not None
    assert not key.port.tasks
