"""What a deletion takes with it.

An artifact carries a receipt: alternative bundles of evidence, each
claimed sufficient on its own. Under a deletion it survives exactly when
some bundle contains nothing dead. An edge additionally dies with either
endpoint. An artifact with no receipt is self-supported and dies only when
named directly. The rule closes transitively, so a summary built on a
merge built on a withdrawn row goes with the row.

Everything here is pure: preview and commit call the same function.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .storage import source_of


@dataclass
class Cascade:
    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.nodes) + len(self.edges)


def _dead_member(store, member: str, dead: set[str], dead_sources: set[str]) -> bool:
    if member in dead:
        return True
    doc = source_of(member)
    if doc is not None:
        return doc in dead_sources
    resolved = store.resolve(member)
    if resolved in dead:
        return True
    art = store.nodes.get(resolved) or store.edge_index.get(resolved)
    # Evidence that no longer exists, or was tombstoned earlier, is dead.
    # Retired evidence is not: a compact summary cites members that compact
    # itself retired, and a merge survivor cites an absorbed node, and both
    # still stand as evidence.
    return art is None or art.deleted_at is not None


def alive(store, art_id: str, dead: set[str], dead_sources: set[str]) -> bool:
    edge = store.edge_index.get(art_id)
    if edge is not None:
        for nid in edge.roles.values():
            if store.resolve(nid) in dead:
                return False
        support = edge.support
    else:
        support = store.nodes[art_id].support
    if not support:
        return True
    for bundle in support:
        if not any(_dead_member(store, m, dead, dead_sources) for m in bundle):
            return True
    return False


def compute(
    store,
    nodes: Iterable[str] = (),
    edges: Iterable[str] = (),
    sources: Iterable[str] = (),
) -> Cascade:
    dead: set[str] = set()
    order: list[str] = []
    work: deque[str] = deque()
    dead_sources = set(sources)

    def take(art_id: str) -> None:
        dead.add(art_id)
        order.append(art_id)
        work.append(art_id)

    for nid in nodes:
        nid = store.resolve(nid)
        if nid in store.nodes and store.nodes[nid].live and nid not in dead:
            take(nid)
    for eid in edges:
        if eid in store.edge_index and store.edge_index[eid].live and eid not in dead:
            take(eid)
    for doc in dead_sources:
        for art_id in sorted(store.sources.get(doc, ())):
            art = store.nodes.get(art_id) or store.edge_index.get(art_id)
            if art is None or not art.live or art_id in dead:
                continue
            if not alive(store, art_id, dead, dead_sources):
                take(art_id)

    while work:
        gone = work.popleft()
        for dep in sorted(store.dependents.get(gone, ())):
            if dep in dead:
                continue
            art = store.nodes.get(dep) or store.edge_index.get(dep)
            if art is None or not art.live:
                continue
            if not alive(store, dep, dead, dead_sources):
                take(dep)

    out = Cascade()
    for art_id in order:
        (out.edges if art_id in store.edge_index else out.nodes).append(art_id)
    return out
