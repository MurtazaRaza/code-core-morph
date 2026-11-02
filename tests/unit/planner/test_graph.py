"""SCC condensation and leaves-first ordering (FR-2.1, FR-5.2, FR-5.6, D-14)."""

from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from codecoremorph.planner.graph import condense, strongly_connected_components


def test_cycles_condense_into_one_cluster() -> None:
    edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set[str]()}
    components = strongly_connected_components("abcde", edges)
    assert sorted(components) == [("a", "b", "c"), ("d",), ("e",)]


def test_leaves_come_first_and_ties_break_by_path() -> None:
    # z depends on nothing, but sorts last among the ready clusters
    edges = {"app": {"lib"}, "lib": {"util"}, "util": set[str](), "z": set[str]()}
    graph = condense(edges, edges)
    assert graph.clusters == (("util",), ("lib",), ("app",), ("z",))
    assert graph.depends_on[graph.cluster_of("app")] == {graph.cluster_of("lib")}
    assert graph.dependents_of(graph.cluster_of("util")) == [graph.cluster_of("lib")]
    assert graph.cyclic == []


def test_a_cycle_is_ordered_after_its_dependencies() -> None:
    edges = {"a.cpp": {"a.h"}, "a.h": {"a.cpp", "base.h"}, "base.h": set[str](), "main": {"a.h"}}
    graph = condense(edges, edges)
    assert graph.clusters == (("base.h",), ("a.cpp", "a.h"), ("main",))
    assert graph.cyclic == [("a.cpp", "a.h")]


def test_edges_to_unknown_nodes_and_self_loops_are_ignored() -> None:
    graph = condense(["a", "b"], {"a": {"a", "zzz"}, "b": {"a"}})
    assert graph.clusters == (("a",), ("b",))
    assert "zzz" not in graph


def test_deep_chains_need_no_recursion() -> None:
    nodes = [f"n{i:05d}" for i in range(5000)]
    edges = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
    graph = condense(nodes, edges)
    assert graph.clusters[0] == (nodes[-1],)
    assert graph.clusters[-1] == (nodes[0],)


@st.composite
def _graphs(draw: st.DrawFn) -> dict[str, set[str]]:
    count = draw(st.integers(min_value=1, max_value=25))
    nodes = [f"f{i:02d}" for i in range(count)]
    pairs = draw(st.lists(st.tuples(st.sampled_from(nodes), st.sampled_from(nodes)), max_size=60))
    edges: dict[str, set[str]] = {n: set() for n in nodes}
    for a, b in pairs:
        edges[a].add(b)
    return edges


@settings(max_examples=200, deadline=None)
@given(_graphs(), st.randoms(use_true_random=False))
def test_every_dependency_comes_first_and_order_is_stable(
    edges: dict[str, set[str]], rng: random.Random
) -> None:
    graph = condense(edges, edges)
    # Leaves first: each cluster depends only on clusters listed before it.
    for position, deps in enumerate(graph.depends_on):
        assert all(d < position for d in deps)
    # Nodes in one cluster reach each other; nodes in different clusters don't both ways.
    for source, targets in edges.items():
        for target in targets:
            assert graph.cluster_of(target) <= graph.cluster_of(source)
    # The same graph, built in another order, gives the same plan order (FR-5.6).
    keys = list(edges)
    rng.shuffle(keys)
    shuffled = {k: rng.sample(sorted(edges[k]), len(edges[k])) for k in keys}
    assert condense(keys, shuffled).clusters == graph.clusters
