"""The planner's view of the project: scope, pairing, graph edges, fan-in and boundaries."""

from __future__ import annotations

from codecoremorph.core.models import Boundary, RuleSite
from codecoremorph.planner.project import ProjectModel

from .conftest import FakeProject


def _geo(p: FakeProject) -> ProjectModel:
    p.file("CMakeLists.txt", "project(geo)\n")
    p.file("include/geo/vector.h", "struct Vector {};\n", public_api=True)
    p.file("src/vector.cpp", '#include "geo/vector.h"\nVector* make() { return new Vector; }\n')
    p.file("include/geo/shape.h", '#include "geo/vector.h"\n', public_api=True)
    p.file("src/shape.cpp", '#include "geo/shape.h"\nint area() { make(); return 0; }\n')
    p.file("src/util.h", "int util();\n")
    p.file("src/util.cpp", "int util() { return 1; }\n")  # same directory: paired
    p.file("tests/vector_test.cpp", '#include "geo/vector.h"\nvoid t() { make(); }\n')
    p.file("third_party/json.h", "// vendored\n")
    p.file("docs/example.cpp", "int main() {}\n")  # outside source_dirs and test_dirs
    p.dep("src/vector.cpp", "include/geo/vector.h")
    p.dep("include/geo/shape.h", "include/geo/vector.h")
    p.dep("src/shape.cpp", "include/geo/shape.h")
    p.dep("tests/vector_test.cpp", "include/geo/vector.h")
    p.dep("src/shape.cpp", "third_party/json.h")
    p.symbol("c:@S@Vector", "include/geo/vector.h", (1, 1), (1, 17), kind="struct")
    p.symbol("c:@F@make", "src/vector.cpp", (2, 1), (2, 39))
    p.symbol("c:@F@make", "include/geo/vector.h", (1, 1), (1, 2), definition=False)  # decl
    p.symbol("c:@F@area", "src/shape.cpp", (2, 1), (2, 35), boundary="c_abi")
    p.symbol("c:@N@geo", "src/vector.cpp", (1, 1), (9, 1), kind="namespace")
    p.ref("src/shape.cpp", "c:@F@make", 2, 14)
    p.ref("tests/vector_test.cpp", "c:@F@make", 2, 12)
    p.build()
    return ProjectModel.load(
        p.index_path, p.worktree, source_dirs=["src", "include"], test_dirs=["tests"]
    )


def test_scope_keeps_editable_code_in_the_project_dirs(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    assert sorted(project.files) == [
        "include/geo/shape.h",
        "include/geo/vector.h",
        "src/shape.cpp",
        "src/util.cpp",
        "src/util.h",
        "src/vector.cpp",
        "tests/vector_test.cpp",
    ]
    assert project.build_files == ["CMakeLists.txt"]
    assert project.chars("src/util.cpp") == len("int util() { return 1; }\n")


def test_headers_pair_with_their_sources(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    assert project.partners == {
        "include/geo/shape.h": ["src/shape.cpp"],
        "include/geo/vector.h": ["src/vector.cpp"],
        "src/shape.cpp": ["include/geo/shape.h"],
        "src/util.cpp": ["src/util.h"],
        "src/util.h": ["src/util.cpp"],
        "src/vector.cpp": ["include/geo/vector.h"],
    }
    graph = project.graph
    # each pair is one cluster; the vector pair comes before everything that uses it
    vector = graph.cluster_of("src/vector.cpp")
    assert graph.cluster_of("include/geo/vector.h") == vector
    assert graph.cluster_of("src/shape.cpp") > vector
    assert graph.cluster_of("tests/vector_test.cpp") > vector


def test_references_add_edges_to_definitions_only(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    # shape.cpp calls make(), defined in vector.cpp (the header only declares it)
    assert "src/vector.cpp" in project.edges["src/shape.cpp"]
    assert "include/geo/vector.h" not in project.edges["src/shape.cpp"]
    assert "third_party/json.h" not in project.edges["src/shape.cpp"]


def test_symbols_at_sites_and_their_fan_in(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    site = RuleSite(file="src/vector.cpp", line=2, column=27, text="new Vector")
    # the innermost symbol, never the enclosing namespace
    assert project.symbols_at([site]) == ["c:@F@make"]
    assert project.fan_in(["c:@F@make"]) == [
        "include/geo/vector.h",
        "src/shape.cpp",
        "src/vector.cpp",
        "tests/vector_test.cpp",
    ]
    assert project.fan_in([]) == []
    outside = RuleSite(file="src/vector.cpp", line=50, column=1)
    assert project.symbols_at([outside]) == []


def test_the_dependency_graph_for_the_dashboard(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    graph = project.dependency_graph()
    assert [f.path for f in graph.files] == sorted(project.files)
    clusters = {f.path: f.cluster for f in graph.files}
    assert clusters["src/vector.cpp"] == clusters["include/geo/vector.h"]  # a pair, one cluster
    assert clusters["src/shape.cpp"] > clusters["src/vector.cpp"]  # leaves first
    assert ("src/shape.cpp", "include/geo/shape.h") in graph.edges
    assert ("src/shape.cpp", "src/vector.cpp") in graph.edges  # a reference edge
    assert graph.edges == sorted(graph.edges)
    again = ProjectModel.load(
        fake_project.index_path,
        fake_project.worktree,
        source_dirs=["src", "include"],
        test_dirs=["tests"],
    )
    assert again.dependency_graph().model_dump_json() == graph.model_dump_json()  # deterministic


def test_boundaries(fake_project: FakeProject) -> None:
    project = _geo(fake_project)
    assert project.boundaries == {"src/shape.cpp": {Boundary.C_ABI}}


def test_dot_means_every_directory(fake_project: FakeProject) -> None:
    fake_project.file("a.cpp", "int a;\n").file("deep/b.cpp", "int b;\n").build()
    project = ProjectModel.load(fake_project.index_path, fake_project.worktree)
    assert sorted(project.files) == ["a.cpp", "deep/b.cpp"]


def test_references_never_override_the_include_structure(fake_project: FakeProject) -> None:
    p = fake_project
    # db.h includes options.h; options.h only forward-declares Snapshot, defined in db.h
    p.file("include/db.h", '#include "options.h"\nclass Snapshot {};\n')
    p.file("include/options.h", "class Snapshot;\nstruct Options { const Snapshot* s; };\n")
    p.file("src/user.cpp", '#include "options.h"\nint f() { Snapshot* s = 0; return 0; }\n')
    p.dep("include/db.h", "include/options.h")
    p.dep("src/user.cpp", "include/options.h")
    p.symbol("c:@S@Snapshot", "include/db.h", (2, 1), (2, 18), kind="class")
    p.ref("include/options.h", "c:@S@Snapshot", 2, 23)
    p.ref("src/user.cpp", "c:@S@Snapshot", 2, 12)
    p.build()
    project = ProjectModel.load(p.index_path, p.worktree)
    assert project.graph.cyclic == []  # no db.h <-> options.h cycle
    assert "include/db.h" not in project.edges["include/options.h"]
    # a reference that agrees with the structure is kept
    assert "include/db.h" in project.edges["src/user.cpp"]
    assert project.graph.cluster_of("src/user.cpp") > project.graph.cluster_of("include/db.h")
