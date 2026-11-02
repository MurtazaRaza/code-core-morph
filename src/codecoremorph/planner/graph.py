"""Directed graphs of files, condensed into a DAG of clusters (FR-2.1, D-14).

Real dependency graphs have cycles, so the planner orders the graph's strongly connected
components (SCCs) instead of its files. Everything here is deterministic: nodes and neighbours
are visited in sorted order, and ties in the topological order break by path (FR-5.6).

Edges point from a node to the nodes it depends on: ``{"a.cpp": {"a.h"}}`` means a.cpp
depends on a.h, so a.h's cluster comes first ("leaves first").
"""

from __future__ import annotations

import heapq
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass


def strongly_connected_components(
    nodes: Iterable[str], edges: Mapping[str, Collection[str]]
) -> list[tuple[str, ...]]:
    """Tarjan's algorithm, iteratively (no recursion limit on deep include chains).

    Each component is sorted; edges to nodes not in ``nodes`` are ignored.
    """
    universe = sorted(set(nodes))
    known = set(universe)
    adjacency = {n: sorted(m for m in edges.get(n, ()) if m in known) for n in universe}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[tuple[str, ...]] = []
    counter = 0
    for root in universe:
        if root in index:
            continue
        # Each frame: a node and the position of the next neighbour to visit.
        work: list[tuple[str, int]] = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, position = work[-1]
            neighbours = adjacency[node]
            if position < len(neighbours):
                work[-1] = (node, position + 1)
                nxt = neighbours[position]
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(tuple(sorted(component)))
    return components


@dataclass(frozen=True)
class ClusterGraph:
    """The condensation: clusters listed leaves first, so every cluster comes after the
    clusters it depends on. Ties break by each cluster's first path."""

    clusters: tuple[tuple[str, ...], ...]
    depends_on: tuple[frozenset[int], ...]  # by position in ``clusters``
    _cluster_of: Mapping[str, int]

    def cluster_of(self, node: str) -> int:
        return self._cluster_of[node]

    def __contains__(self, node: object) -> bool:
        return node in self._cluster_of

    def dependents_of(self, cluster: int) -> list[int]:
        return [i for i, deps in enumerate(self.depends_on) if cluster in deps]

    @property
    def cyclic(self) -> list[tuple[str, ...]]:
        """Clusters of more than one node: the cycles that were condensed."""
        return [c for c in self.clusters if len(c) > 1]

    def closure(self) -> list[int]:
        """Each cluster's transitive dependencies, as a bit set over cluster positions."""
        masks: list[int] = []
        for deps in self.depends_on:  # leaves first: dependencies are already computed
            mask = 0
            for d in deps:
                mask |= masks[d] | (1 << d)
            masks.append(mask)
        return masks


def condense(nodes: Iterable[str], edges: Mapping[str, Collection[str]]) -> ClusterGraph:
    """Condense ``nodes`` and ``edges`` into a :class:`ClusterGraph`."""
    components = strongly_connected_components(nodes, edges)
    component_of = {node: i for i, comp in enumerate(components) for node in comp}
    deps: list[set[int]] = [set() for _ in components]
    for node, targets in edges.items():
        source = component_of.get(node)
        if source is None:
            continue
        for target in targets:
            other = component_of.get(target)
            if other is not None and other != source:
                deps[source].add(other)
    dependents: list[list[int]] = [[] for _ in components]
    for source, targets in enumerate(deps):
        for target in targets:
            dependents[target].append(source)

    # Kahn's algorithm over "depends on" edges; the heap orders ready clusters by first path.
    waiting = [len(d) for d in deps]
    ready = [(components[i][0], i) for i, count in enumerate(waiting) if count == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        _, current = heapq.heappop(ready)
        order.append(current)
        for dependent in dependents[current]:
            waiting[dependent] -= 1
            if waiting[dependent] == 0:
                heapq.heappush(ready, (components[dependent][0], dependent))
    position = {old: new for new, old in enumerate(order)}
    clusters = tuple(components[i] for i in order)
    return ClusterGraph(
        clusters=clusters,
        depends_on=tuple(frozenset(position[d] for d in deps[i]) for i in order),
        _cluster_of={node: position[component_of[node]] for node in component_of},
    )
