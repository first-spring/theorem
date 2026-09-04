"""Write surface: every verb returns a structured receipt.

The receipt is the system's half of the conversation: what changed, which
guards ran, and any duplicate candidates the write triggered.
"""

from __future__ import annotations

import csv
import difflib
from dataclasses import dataclass, field

from ..ast_nodes import (
    AssertEdge,
    AssertNode,
    Compact,
    Delete,
    DeriveClass,
    DeriveEdge,
    Distinct,
    Flag,
    Merge,
    Refine,
    RenameClass,
    RenameProp,
    Restore,
    Retire,
    SetProps,
    Stmt,
    Withdraw,
)
from ..schema import ClassDef, EdgeDef, Schema
from ..verifier import VerifyError, _check_literal_type
from . import dedup
from .storage import Store, source_of

PROVISIONAL_QUOTA = 500


class WriteError(Exception):
    pass


@dataclass
class WriteContext:
    store: Store
    schema: Schema
    env: dict[str, object] = field(
        default_factory=dict
    )  # name -> node id | list[node id]

    def resolve_ref(self, ref: str) -> str:
        if ref.startswith("#"):
            nid = self.store.resolve(ref)
            if nid not in self.store.nodes:
                raise WriteError(f"unknown node id {ref}")
            return nid
        value = self.env.get(ref)
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if isinstance(value, str):
            return self.store.resolve(value)
        if isinstance(value, list):
            raise WriteError(
                f'"{ref}" is bound to {len(value)} nodes; need exactly one'
            )
        raise WriteError(f'"{ref}" is not bound to a node')


@dataclass
class Receipt:
    lines: list[str]
    dup_candidates: list[dict] = field(default_factory=list)

    def render(self) -> str:
        out = list(self.lines)
        if self.dup_candidates:
            out.append(f"  dup candidates: {len(self.dup_candidates)}")
            for c in self.dup_candidates:
                out.append(f'    {c["a"]} {c["cls"]} "{c["name"]}" score {c["score"]}')
                if c.get("evidence"):
                    out.append(f"      {c['evidence']}")
            out.append("  resolve with: merge / distinct")
        return "\n".join(out)


def _resolve_support(ctx: WriteContext, bundles: list[list[str]]) -> list[list[str]]:
    """Node ids in a bundle must name live artifacts; provenance is taken as given."""
    out = []
    for bundle in bundles:
        members = []
        for member in bundle:
            if member.startswith("#"):
                rid = ctx.store.resolve(member)
                art = ctx.store.nodes.get(rid) or ctx.store.edge_index.get(member)
                if art is None or not art.live:
                    raise WriteError(f"receipt cites {member}, which is not a live artifact")
                members.append(rid if rid in ctx.store.nodes else member)
            else:
                members.append(member)
        out.append(members)
    from .storage import cap_bundles

    return cap_bundles(out)


def _first_provenance(bundles: list[list[str]]) -> str | None:
    for bundle in bundles:
        for member in bundle:
            if not member.startswith("#"):
                return member
    return None


def _quota_check(ctx: WriteContext, cls: str) -> None:
    cdef = ctx.schema.classes[cls]
    if cdef.status == "provisional" and cdef.quota is not None:
        count = len(ctx.store.live_by_class(cls))
        if count >= cdef.quota:
            raise WriteError(
                f"class {cls} is provisional and at quota ({cdef.quota} instances)"
            )


def _load_attachment(ctx: WriteContext, ref: str) -> list[dict]:
    key = ref.split(":", 1)[1]
    attach_dir = (ctx.store.path / "attachments").resolve()
    path = (attach_dir / f"{key}.csv").resolve()
    if not path.is_relative_to(attach_dir):
        raise WriteError(f"attachment key {key!r} escapes the attachments directory")
    if not path.exists():
        raise WriteError(f"attachment {ref} not found at {path}")
    # utf-8-sig: a BOM would otherwise become part of the first header name
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _assert_node(stmt: AssertNode, ctx: WriteContext) -> Receipt:
    store = ctx.store
    _quota_check(ctx, stmt.cls)
    props = dict(stmt.props)
    state = "atom"
    rows = None
    for value in props.values():
        if isinstance(value, str) and value.startswith("attach:"):
            rows = _load_attachment(ctx, value)
            state = "blob"
    if stmt.cls == "table_blob":
        state = "blob"
    nid = store.next_id(stmt.cls)
    support = _resolve_support(ctx, stmt.support)
    record = {
        "op": "put_node",
        "id": nid,
        "cls": stmt.cls,
        "props": props,
        "state": state,
        "support": support,
    }
    provenance = _first_provenance(support)
    if provenance:
        props["_source"] = provenance  # what `merge ... prefer source` reads
    if rows is not None:
        props["_rows"] = rows
    pos = store.apply(record)
    ctx.env[stmt.name] = nid
    threshold = ctx.schema.classes[stmt.cls].dedup_threshold or dedup.SIM_THRESHOLD
    cands = dedup.sync_candidates(store, store.nodes[nid], threshold=threshold)
    dedup.record(store, cands)
    display = [
        {**c, "name": store.nodes[c["a"]].props.get("name", c["a"])} for c in cands
    ]
    return Receipt(
        [
            f"receipt: created {stmt.cls} {stmt.name} = {nid} at @t-{pos}",
            "  guards: schema ok, class invariants ok",
        ],
        display,
    )


def _assert_edge(stmt: AssertEdge, ctx: WriteContext) -> Receipt:
    store = ctx.store
    roles = {role: ctx.resolve_ref(ref) for role, ref in stmt.role_refs.items()}
    edef = ctx.schema.edges[stmt.edge]
    for role, nid in roles.items():
        want = edef.roles[role]
        got = store.nodes[nid].cls
        base_chain = {got}
        c = ctx.schema.classes.get(got)
        while c is not None and c.base:
            base_chain.add(c.base)
            c = ctx.schema.classes.get(c.base)
        if want not in base_chain:
            raise WriteError(
                f"role {role} of {stmt.edge} takes a {want}; {nid} is a {got}"
            )
    eid = store.next_id("edge")
    support = _resolve_support(ctx, stmt.support)
    pos = store.apply(
        {"op": "put_edge", "id": eid, "type": stmt.edge, "roles": roles, "support": support}
    )
    role_str = ", ".join(f"{r}: {n}" for r, n in roles.items())
    return Receipt(
        [
            f"receipt: created edge {stmt.edge}({role_str}) = {eid} at @t-{pos}",
            "  guards: schema ok, roles ok",
        ]
    )


def _merge(stmt: Merge, ctx: WriteContext) -> Receipt:
    store = ctx.store
    a = ctx.resolve_ref(stmt.a)
    b = ctx.resolve_ref(stmt.b)
    if a == b:
        # the user usually means "merge with my duplicate": consult the ledger
        pending = []
        for rec in ctx.store.dup_ledger:
            ra, rb = ctx.store.resolve(rec["a"]), ctx.store.resolve(rec["b"])
            pair = frozenset((ra, rb))
            if a in pair and len(pair) == 2 and pair not in ctx.store.distinct_pairs:
                pending.append((rec["score"], rb if ra == a else ra))
        hint = ""
        if pending:
            _, other = max(pending)
            name = ctx.store.nodes[other].props.get("name", other)
            hint = (
                f'; {a} has a pending dup candidate {other} ("{name}"). '
                f"did you mean: merge {a}, {other}"
            )
        raise WriteError(f"merge of a node with itself{hint}")
    na, nb = store.nodes[a], store.nodes[b]
    if na.cls != nb.cls:
        raise WriteError(f"cannot merge {na.cls} with {nb.cls}")
    survivor, absorbed = (na, nb) if na.created_at <= nb.created_at else (nb, na)
    pre_states = {
        n.id: {
            "props": {k: v for k, v in n.props.items() if not k.startswith("_")},
            "state": n.state,
            "created_at": n.created_at,
        }
        for n in (na, nb)
    }
    # property reconciliation
    merged: dict[str, object] = {}
    if stmt.policy == "newest":
        newer, older = (na, nb) if na.created_at > nb.created_at else (nb, na)
        for k, v in older.props.items():
            merged[k] = v
        for k, v in newer.props.items():
            merged[k] = v
    elif stmt.policy.startswith("source "):
        want_src = stmt.policy.split(" ", 1)[1]
        if na.props.get("_source") == want_src:
            preferred, other = na, nb
        elif nb.props.get("_source") == want_src:
            preferred, other = nb, na
        else:
            raise WriteError(
                f"neither node has source {want_src}; "
                f"use prefer newest or explicit values"
            )
        merged = {**other.props, **preferred.props}
    else:
        raise WriteError(f"unknown merge policy {stmt.policy!r}")
    merged = {k: v for k, v in merged.items() if not k.startswith("_")}
    store.apply(
        {
            "op": "lineage",
            "kind": "merge",
            "survivor": survivor.id,
            "absorbed": absorbed.id,
            "pre_states": pre_states,
            "policy": stmt.policy,
        }
    )
    # re-home absorbed node's edges onto the survivor
    for edge in list(store.edges.get(absorbed.id, [])):
        if not edge.live:
            continue
        new_roles = {
            r: (survivor.id if store.resolve(nid) == absorbed.id else nid)
            for r, nid in edge.roles.items()
        }
        store.apply({"op": "retire_edge", "id": edge.id})
        store.apply(
            {
                "op": "put_edge",
                "id": store.next_id("edge"),
                "type": edge.type,
                "roles": new_roles,
                "support": edge.support,
            }
        )
    store.apply({"op": "alias", "absorbed": absorbed.id, "survivor": survivor.id})
    union = [b for b in na.support if b] + [b for b in nb.support if b not in na.support]
    pos = store.apply(
        {"op": "patch_node", "id": survivor.id, "props": merged, "support": union}
    )
    store.apply(
        {"op": "retire", "id": absorbed.id, "reason": f"merged into {survivor.id}"}
    )
    return Receipt(
        [
            f"receipt: merged -> {survivor.id} (lineage keeps both) at @t-{pos}",
            f"  absorbed {absorbed.id} is now an alias of {survivor.id}",
            f"  policy: prefer {stmt.policy}",
        ]
    )


def _distinct(stmt: Distinct, ctx: WriteContext) -> Receipt:
    a = ctx.resolve_ref(stmt.a)
    b = ctx.resolve_ref(stmt.b)
    if a == b:
        raise WriteError(
            f"distinct needs two different nodes; both refs resolve to {a}"
        )
    pos = ctx.store.apply({"op": "distinct", "a": a, "b": b, "reason": stmt.reason})
    ctx.store.dup_ledger[:] = [
        r
        for r in ctx.store.dup_ledger
        if frozenset((ctx.store.resolve(r["a"]), ctx.store.resolve(r["b"])))
        != frozenset((a, b))
    ]
    return Receipt(
        [
            f"receipt: distinct {a}, {b} recorded at @t-{pos}",
            "  pair suppressed from future dup candidates",
        ]
    )


def _coerce(value: str, typ: str):
    if typ == "int":
        return int(value)
    if typ == "float":
        return float(value)
    if typ == "bool":
        return value.strip().lower() in ("true", "1", "yes")
    return value


def _refine(stmt: Refine, ctx: WriteContext) -> Receipt:
    store, schema = ctx.store, ctx.schema
    blob_id = ctx.resolve_ref(stmt.ref)
    blob = store.nodes[blob_id]
    rows = blob.props.get("_rows")
    if not rows:
        raise WriteError(f"{blob_id} has no tabular payload to refine")
    target_props = schema.all_props(stmt.into_cls)
    for target, source_col in stmt.mapping.items():
        if target not in target_props:
            raise WriteError(f"class {stmt.into_cls} has no property {target}")
        if source_col not in rows[0]:
            raise WriteError(
                f"payload has no column {source_col!r}; columns are {list(rows[0])}"
            )
    child_ids = []
    for row in rows:
        props = {t: _coerce(row[s], target_props[t]) for t, s in stmt.mapping.items()}
        cid = store.next_id(stmt.into_cls)
        store.apply(
            {
                "op": "put_node",
                "id": cid,
                "cls": stmt.into_cls,
                "props": props,
                "state": "atom",
                "origin": blob_id,
                "support": [[blob_id]],
            }
        )
        store.apply(
            {
                "op": "lineage",
                "kind": "refine",
                "parent": blob_id,
                "child": cid,
                "mapping": stmt.mapping,
            }
        )
        child_ids.append(cid)
    pos = store.apply(
        {"op": "patch_node", "id": blob_id, "props": {}, "state": "composite"}
    )
    n_dups = 0
    for cid in child_ids:
        cands = dedup.sync_candidates(store, store.nodes[cid])
        cands = [c for c in cands if c["a"] not in child_ids]
        dedup.record(store, cands)
        n_dups += len(cands)
    ctx.env[stmt.name] = child_ids
    lines = [
        f"receipt: refined {blob_id} -> {len(child_ids)} {stmt.into_cls} nodes at @t-{pos}",
        f"  lineage: each new node carries origin {blob_id}",
    ]
    if n_dups:
        lines.append(f"  dup candidates: {n_dups} (queued per class)")
    lines.append("  blob state: composite, retained as lineage parent (cold)")
    return Receipt(lines)


def _compact(stmt: Compact, ctx: WriteContext) -> Receipt:
    store = ctx.store
    members = ctx.env.get(stmt.src)
    if isinstance(members, str):
        members = [members]
    if not isinstance(members, list) or not members:
        raise WriteError(f'"{stmt.src}" is not bound to a node set')
    classes = {store.nodes[store.resolve(m)].cls for m in members}
    if len(classes) != 1:
        raise WriteError(f"compact needs one class, got {sorted(classes)}")
    cls = classes.pop()
    sid = store.next_id(cls)
    support = _resolve_support(ctx, stmt.support) or [
        [store.resolve(m) for m in members]
    ]
    store.apply(
        {
            "op": "put_node",
            "id": sid,
            "cls": cls,
            "props": dict(stmt.props),
            "state": "composite",
            "support": support,
        }
    )
    for m in members:
        mid = store.resolve(m)
        store.apply({"op": "lineage", "kind": "compact", "parent": sid, "child": mid})
        store.apply({"op": "retire", "id": mid, "reason": f"compacted into {sid}"})
    pos = store.position
    ctx.env[stmt.name] = sid
    return Receipt(
        [
            f"receipt: compacted {len(members)} {cls} nodes -> {sid} at @t-{pos}",
            "  members retired, reachable through lineage",
        ]
    )


def _retire(stmt: Retire, ctx: WriteContext) -> Receipt:
    nid = ctx.resolve_ref(stmt.ref)
    pos = ctx.store.apply({"op": "retire", "id": nid, "reason": stmt.reason})
    return Receipt(
        [
            f"receipt: retired {nid} at @t-{pos}",
            f"  reason: {stmt.reason}",
            "  node remains queryable through lineage; excluded from current reads",
        ]
    )


def _flag(stmt: Flag, ctx: WriteContext) -> Receipt:
    nid = ctx.resolve_ref(stmt.ref)
    pos = ctx.store.apply({"op": "flag", "id": nid, "reason": stmt.reason})
    return Receipt([f"receipt: flagged {nid} at @t-{pos}", "  health.query updated"])


def _set(stmt: SetProps, ctx: WriteContext) -> Receipt:
    store = ctx.store
    nid = ctx.resolve_ref(stmt.ref)
    node = store.nodes[nid]
    if not node.live:
        raise WriteError(f"{stmt.ref} ({nid}) is deleted or retired")
    all_props = ctx.schema.all_props(node.cls)
    for prop, value in stmt.props.items():
        if prop not in all_props:
            raise WriteError(f'unknown property "{prop}" on class {node.cls}')
        if value is not None:
            # `set #s-1 {...}` names no binding, so the verifier could not
            # type it; the node's class is known here, so check it now.
            try:
                _check_literal_type(value, all_props[prop], prop, node.cls, stmt.line)
            except VerifyError as e:
                raise WriteError(str(e).split("\n")[0].removeprefix("error: ")) from e
    values = {k: v for k, v in stmt.props.items() if v is not None}
    unset = [k for k, v in stmt.props.items() if v is None]
    pos = store.apply({"op": "patch_node", "id": nid, "props": values, "unset": unset})
    changed = ", ".join(sorted(values)) or "nothing"
    removed = f"; removed {', '.join(unset)}" if unset else ""
    return Receipt([f"receipt: set {nid} at @t-{pos}", f"  changed: {changed}{removed}"])


def _cascade_lines(store, cascade) -> list[str]:
    lines = [f"  cascade: {len(cascade.nodes)} nodes, {len(cascade.edges)} edges"]
    groups: dict[str, list[str]] = {}
    for nid in cascade.nodes:
        groups.setdefault(store.nodes[nid].cls, []).append(nid)
    for eid in cascade.edges:
        groups.setdefault(store.edge_index[eid].type, []).append(eid)
    for name, ids in groups.items():
        shown = ", ".join(ids[:20])
        more = f" and {len(ids) - 20} more" if len(ids) > 20 else ""
        lines.append(f"    {name}: {shown}{more}")
    return lines

def _delete(stmt: Delete, ctx: WriteContext) -> Receipt:
    store = ctx.store
    if stmt.kind == "edge":
        edge = store.edge_index.get(stmt.ref)
        if edge is None or not edge.live:
            raise WriteError(f"no live edge {stmt.ref}")
        target, cascade = stmt.ref, store.cascade(edges=[stmt.ref])
    else:
        nid = ctx.resolve_ref(stmt.ref)
        if not store.nodes[nid].live:
            raise WriteError(f"{stmt.ref} ({nid}) is already deleted or retired")
        target, cascade = nid, store.cascade(nodes=[nid])
    if stmt.preview:
        return Receipt([f"preview: delete {target} would remove", *_cascade_lines(store, cascade)])
    pos = store.apply(
        {
            "op": "delete",
            "id": target,
            "kind": stmt.kind,
            "reason": stmt.reason,
            "cascade": [["node", n] for n in cascade.nodes] + [["edge", e] for e in cascade.edges],
        }
    )
    return Receipt(
        [
            f"receipt: deleted {target} at @t-{pos}",
            f"  reason: {stmt.reason}",
            *_cascade_lines(store, cascade),
            f"  restore with: restore @t-{pos}",
        ]
    )

def _withdraw(stmt: Withdraw, ctx: WriteContext) -> Receipt:
    store = ctx.store
    cascade = store.cascade(sources=[stmt.source])
    if stmt.preview:
        return Receipt([f"preview: withdraw {stmt.source} would remove", *_cascade_lines(store, cascade)])
    pos = store.apply(
        {
            "op": "withdraw",
            "source": stmt.source,
            "reason": stmt.reason,
            "cascade": [["node", n] for n in cascade.nodes] + [["edge", e] for e in cascade.edges],
        }
    )
    return Receipt(
        [
            f"receipt: withdrew {stmt.source} at @t-{pos}",
            f"  reason: {stmt.reason}",
            *_cascade_lines(store, cascade),
            f"  restore with: restore @t-{pos}",
        ]
    )


def _restore(stmt: Restore, ctx: WriteContext) -> Receipt:
    from .cascade import alive

    store = ctx.store
    if stmt.position in store.drops:
        raise WriteError(f"@t-{stmt.position} dropped a class or edge type; derive it again and reload")
    gone = store._deleted_by(stmt.position)
    if not gone:
        raise WriteError(f"nothing was deleted at @t-{stmt.position}")
    # Evaluate aliveness as it will read right after this restore lands: a
    # bundle citing a sibling victim of this same deletion must see that
    # sibling as live again, not as dead from the pre-restore store, which
    # `store.apply` has not touched yet. `cascade.alive()` falls back to the
    # live artifact's own `deleted_at` for ids outside the `dead` set it is
    # given, so a sibling still tombstoned in memory reads dead regardless
    # of that set; clearing this deletion's own victims first, then putting
    # them back before the durable write, closes that gap without touching
    # the shared cascade code the forward delete also relies on.
    #
    # This mutates shared Node/Edge objects in place for the span of the
    # computation below, so a read of the store during that span would see
    # a half-restored state. That is safe only because the store is
    # single-writer: it holds an exclusive process-level lock for its whole
    # lifetime (`storage._acquire_lock`), and a session executes statements
    # synchronously, single-threaded, so nothing else runs while this
    # window is open. If a concurrent reader is ever added inside the same
    # locked process (a background thread, an async read path), this
    # window has to be closed first, e.g. by computing over a shadow copy
    # instead of mutating the live objects.
    original = {a.id: (a.deleted_at, a.deleted_by) for a in gone}
    try:
        for a in gone:
            a.deleted_at = None
            a.deleted_by = None
        still_dead = {
            a.id
            for a in [*store.nodes.values(), *store.edge_index.values()]
            if a.deleted_at is not None
        }
        dead_sources = {src for pos, src in store.withdrawn.items() if pos != stmt.position}
        kept: list[list] = []
        for art in gone:
            if alive(store, art.id, still_dead, dead_sources):
                continue
            # Attribute it to the newest deletion among its dead evidence, so
            # restoring that one brings it back.
            dead_ids_by = [
                (store.nodes.get(m) or store.edge_index.get(m)).deleted_by
                for bundle in art.support
                for m in bundle
                if m.startswith("#")
                and (store.nodes.get(m) or store.edge_index.get(m)) is not None
                and (store.nodes.get(m) or store.edge_index.get(m)).deleted_at is not None
            ]
            if dead_ids_by:
                by = max(dead_ids_by)
            else:
                # Nothing but provenance tokens in its evidence; it is still
                # dead because a withdrawal it cites is still in force. Blame
                # the newest one, so restoring that is what brings it back.
                withdrawal_positions = [
                    pos
                    for pos, src in store.withdrawn.items()
                    if pos != stmt.position
                    and any(source_of(m) == src for bundle in art.support for m in bundle)
                ]
                by = max(withdrawal_positions) if withdrawal_positions else stmt.position
            kept.append(["edge" if art.id in store.edge_index else "node", art.id, by])
    finally:
        for a in gone:
            a.deleted_at, a.deleted_by = original[a.id]
    pos = store.apply({"op": "restore", "of": stmt.position, "kept_dead": kept})
    kept_ids = {k[1] for k in kept}
    restored = [a for a in gone if a.id not in kept_ids]
    nodes = sum(1 for a in restored if a.id in store.nodes)
    edges = len(restored) - nodes
    lines = [f"receipt: restored {nodes} nodes, {edges} edges from @t-{stmt.position} at @t-{pos}"]
    if kept:
        lines.append(f"  still dead: {len(kept)}, their evidence is gone; restore the later deletion first")
        for _kind, art_id, by in kept[:20]:
            lines.append(f"    {art_id} (needs restore @t-{by})")
    return Receipt(lines)


def _derive(stmt: DeriveClass, ctx: WriteContext) -> Receipt:
    schema = ctx.schema
    # durable record FIRST: if the append fails, the live schema must not
    # hold a class that would silently vanish on restart
    quota = stmt.quota or PROVISIONAL_QUOTA
    pos = ctx.store.apply(
        {
            "op": "lineage",
            "kind": "derive_class",
            "child": stmt.name,
            "parent": stmt.base,
            "props": stmt.props,
            "quota": quota,
            "dedup": stmt.dedup,
        }
    )
    schema.classes[stmt.name] = ClassDef(
        name=stmt.name,
        props=dict(stmt.props),
        base=stmt.base,
        status="provisional",
        quota=quota,
        dedup_threshold=stmt.dedup,
    )
    lines = [
        f"receipt: class {stmt.name} provisional at @t-{pos}",
        f"  quota: {quota} instances; promotion needs stable use + dedup review",
    ]
    similar = difflib.get_close_matches(
        stmt.name, [c for c in schema.classes if c != stmt.name], n=1, cutoff=0.7
    )
    if similar:
        score = difflib.SequenceMatcher(None, stmt.name, similar[0]).ratio()
        lines.append(
            f'  note: similar existing class "{similar[0]}" '
            f"(score {score:.2f}), review advised"
        )
    return Receipt(lines)


def _derive_edge(stmt: DeriveEdge, ctx: WriteContext) -> Receipt:
    schema = ctx.schema
    # durable record FIRST: if the append fails, the live schema must not
    # hold an edge that would silently vanish on restart
    pos = ctx.store.apply(
        {
            "op": "lineage",
            "kind": "derive_edge",
            "name": stmt.name,
            "roles": stmt.roles,
        }
    )
    schema.edges[stmt.name] = EdgeDef(stmt.name, dict(stmt.roles))
    return Receipt([f"receipt: edge {stmt.name} declared at @t-{pos}"])


def _rename_class(stmt: RenameClass, ctx: WriteContext) -> Receipt:
    # durable record FIRST, matching _derive: the live schema must not move
    # a class that would silently vanish on restart
    pos = ctx.store.apply({"op": "lineage", "kind": "rename_class", "name": stmt.name, "to": stmt.to})
    ctx.schema.rename_class(stmt.name, stmt.to)
    count = len(ctx.store.by_class.get(stmt.to, []))
    return Receipt([f"receipt: renamed class {stmt.name} to {stmt.to} at @t-{pos}", f"  {count} nodes keep their ids"])

def _rename_prop(stmt: RenameProp, ctx: WriteContext) -> Receipt:
    pos = ctx.store.apply(
        {"op": "lineage", "kind": "rename_prop", "cls": stmt.cls, "name": stmt.name, "to": stmt.to}
    )
    ctx.schema.rename_prop(stmt.cls, stmt.name, stmt.to)
    return Receipt([f"receipt: renamed property {stmt.cls}.{stmt.name} to {stmt.to} at @t-{pos}"])

def deprecate_class(session, name: str) -> str:
    cdef = session.schema.classes[name]
    pos = session.store.apply(
        {"op": "lineage", "kind": "deprecate_class", "name": name}
    )
    cdef.status = "deprecated"
    return f"receipt: class {name} deprecated at @t-{pos}; existing nodes kept, new asserts rejected"


def execute_write(stmt: Stmt, ctx: WriteContext) -> Receipt:
    match stmt:
        case AssertNode():
            return _assert_node(stmt, ctx)
        case AssertEdge():
            return _assert_edge(stmt, ctx)
        case Merge():
            return _merge(stmt, ctx)
        case Distinct():
            return _distinct(stmt, ctx)
        case Refine():
            return _refine(stmt, ctx)
        case Compact():
            return _compact(stmt, ctx)
        case Retire():
            return _retire(stmt, ctx)
        case Flag():
            return _flag(stmt, ctx)
        case SetProps():
            return _set(stmt, ctx)
        case Delete():
            return _delete(stmt, ctx)
        case Withdraw():
            return _withdraw(stmt, ctx)
        case Restore():
            return _restore(stmt, ctx)
        case DeriveClass():
            return _derive(stmt, ctx)
        case DeriveEdge():
            return _derive_edge(stmt, ctx)
        case RenameClass():
            return _rename_class(stmt, ctx)
        case RenameProp():
            return _rename_prop(stmt, ctx)
        case _:
            raise WriteError(f"not a write statement: {type(stmt).__name__}")
