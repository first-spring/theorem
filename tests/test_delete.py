from theorem.engine.storage import Store

from tests.test_receipts import link, put


def test_figure_one_from_the_paper(tmp_path):
    """X and Y support Z; withdrawing X keeps Z, withdrawing both kills it."""
    store = Store(tmp_path)
    z = put(store, "supplier", support=[["doc:x.pdf#p1"], ["doc:y.pdf#p3"]], name="Z")
    only_x = store.cascade(sources=["doc:x.pdf"])
    assert only_x.nodes == [] and only_x.edges == []
    both = store.cascade(sources=["doc:x.pdf", "doc:y.pdf"])
    assert both.nodes == [z]


def test_an_edge_dies_with_either_endpoint(tmp_path):
    store = Store(tmp_path)
    p = put(store, "part", name="cell")
    s = put(store, "supplier", name="Ionix")
    e = link(store, "supplied_by", item=p, source=s)
    out = store.cascade(nodes=[s])
    assert out.nodes == [s]
    assert out.edges == [e]


def test_the_cascade_closes_transitively(tmp_path):
    store = Store(tmp_path)
    row = put(store, "supplier", support=[["doc:a.csv#r1"]], name="Ionix")
    merged = put(store, "supplier", support=[[row]], name="Ionix Group")
    summary = put(store, "supplier", support=[[merged]], name="summary")
    out = store.cascade(sources=["doc:a.csv"])
    assert out.nodes == [row, merged, summary]


def test_a_self_supported_artifact_survives_everything_but_a_direct_delete(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    b = put(store, "supplier", name="B")
    assert store.cascade(sources=["doc:a.csv"]).nodes == [a]
    assert store.cascade(nodes=[b]).nodes == [b]


def test_one_untouched_bundle_keeps_an_artifact_alive(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    b = put(store, "supplier", support=[["doc:b.csv#r1"]], name="B")
    z = put(store, "supplier", support=[[a], [b]], name="Z")
    assert store.cascade(nodes=[a]).nodes == [a]
    assert store.cascade(nodes=[a, b]).nodes == [a, b, z]


def test_already_dead_artifacts_are_not_listed_again(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    store.nodes[a].deleted_at = 1
    assert store.cascade(sources=["doc:a.csv"]).nodes == []


def test_multi_source_cascade_orders_by_document_not_hash(tmp_path):
    """Seeding order must not depend on set iteration order of the sources."""
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    b = put(store, "supplier", support=[["doc:b.csv#r1"]], name="B")
    out = store.cascade(sources=["doc:b.csv", "doc:a.csv"])
    assert out.nodes == [a, b]


def test_retired_evidence_still_counts(tmp_path):
    """compact retires its members; a summary they support must survive a
    deletion that makes the cascade look at it."""
    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    b = put(store, "supplier", support=[["doc:b.csv#r1"]], name="B")
    summary = put(store, "supplier", support=[[a], [b]], name="AB")
    store.apply({"op": "retire", "id": a, "reason": "compacted"})
    # deleting b makes the walk evaluate summary; its other bundle cites the
    # retired a, which is still evidence, so summary lives
    out = store.cascade(nodes=[b])
    assert out.nodes == [b]
    assert store.nodes[summary].live


def test_a_bundle_citing_an_absorbed_id_follows_the_survivor(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="Ionix")
    b = put(store, "supplier", name="Ionix Co")
    z = put(store, "supplier", support=[[b]], name="Z")
    store.apply({"op": "alias", "absorbed": b, "survivor": a})
    store.apply({"op": "retire", "id": b, "reason": f"merged into {a}"})
    assert store.cascade(nodes=[a]).nodes == [a, z]


def test_delete_record_tombstones_the_whole_cascade(tmp_path):
    store = Store(tmp_path)
    p = put(store, "part", name="cell")
    s = put(store, "supplier", name="Ionix")
    e = link(store, "supplied_by", item=p, source=s)
    out = store.cascade(nodes=[s])
    pos = store.apply(
        {"op": "delete", "id": s, "kind": "node", "reason": "test",
         "cascade": [["node", n] for n in out.nodes] + [["edge", x] for x in out.edges]}
    )
    assert store.nodes[s].deleted_at == pos and store.nodes[s].deleted_by == pos
    assert store.edge_index[e].deleted_by == pos
    assert store.nodes[p].live is True


def test_restore_brings_back_exactly_one_deletion(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    b = put(store, "supplier", name="B")
    first = store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    store.apply({"op": "delete", "id": b, "kind": "node", "reason": "", "cascade": [["node", b]]})
    store.apply({"op": "restore", "of": first})
    assert store.nodes[a].live is True and store.nodes[a].deleted_by is None
    assert store.nodes[b].live is False


def test_withdraw_records_the_source_and_restore_forgets_it(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    pos = store.apply({"op": "withdraw", "source": "doc:a.csv", "reason": "", "cascade": [["node", a]]})
    assert store.withdrawn == {pos: "doc:a.csv"}
    assert store.nodes[a].live is False
    store.apply({"op": "restore", "of": pos})
    assert store.withdrawn == {} and store.nodes[a].live is True


def test_delete_of_a_dead_or_unknown_artifact_is_refused(tmp_path):
    import pytest

    from theorem.engine.storage import StoreError

    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    with pytest.raises(StoreError):
        store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    with pytest.raises(StoreError):
        store.apply({"op": "restore", "of": 999})


def test_restore_keeps_an_artifact_dead_when_its_evidence_still_is(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    b = put(store, "supplier", name="B")
    z = put(store, "supplier", support=[[a, b]], name="Z")  # one bundle needing both
    pos_a = store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a], ["node", z]]})
    pos_b = store.apply({"op": "delete", "id": b, "kind": "node", "reason": "", "cascade": [["node", b]]})
    store.apply({"op": "restore", "of": pos_a, "kept_dead": [["node", z, pos_b]]})
    assert store.nodes[a].live is True
    assert store.nodes[z].live is False and store.nodes[z].deleted_by == pos_b
    store.apply({"op": "restore", "of": pos_b, "kept_dead": []})
    assert store.nodes[z].live is True


def test_patch_edge_replaces_the_receipt(tmp_path):
    store = Store(tmp_path)
    p = put(store, "part", name="cell")
    s = put(store, "supplier", name="Ionix")
    e = link(store, "supplied_by", support=[["doc:a.csv#r1"]], item=p, source=s)
    store.apply({"op": "patch_edge", "id": e, "support": [["doc:a.csv#r1"], ["doc:b.csv#r4"]]})
    assert store.edge_index[e].support == [["doc:a.csv#r1"], ["doc:b.csv#r4"]]
    assert e in store.dependents["doc:b.csv#r4"] and e in store.sources["doc:b.csv"]


def test_live_views_skip_tombstones(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    b = put(store, "supplier", name="B")
    store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    assert [n.id for n in store.live_nodes()] == [b]
    assert store.live_by_class("supplier") == [b]


def test_patch_node_can_unset_a_property_and_replace_the_receipt(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A", country="DE")
    store.apply({"op": "patch_node", "id": a, "props": {}, "unset": ["country"], "support": [["doc:b.csv#r2"]]})
    assert "country" not in store.nodes[a].props
    assert store.nodes[a].support == [["doc:b.csv#r2"]]
    assert a not in store.dependents.get("doc:a.csv#r1", set())
    assert store.dependents["doc:b.csv#r2"] == {a}


def test_tombstones_survive_snapshot_and_reopen(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="A")
    pos = store.apply({"op": "withdraw", "source": "doc:a.csv", "reason": "", "cascade": [["node", a]]})
    store.snapshot()
    store.close()
    again = Store(tmp_path)
    assert again.nodes[a].live is False and again.nodes[a].deleted_by == pos
    assert again.withdrawn == {pos: "doc:a.csv"}


def test_reads_skip_deleted_nodes_and_edges(tmp_path):
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} as a')
    s.execute('assert part {name: "cell"} as p')
    s.execute("assert edge supplied_by(item: p, source: a)")
    a = s.store.by_name[("supplier", "ionix")][0]
    out = s.store.cascade(nodes=[a])
    s.store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", n] for n in out.nodes] + [["edge", e] for e in out.edges]})
    assert s.rows("find supplier as x\nreturn x.name") == []
    assert s.rows("find part as p\nfollow p supplied_by source as sup\nreturn sup.name") == []
    s.close()


def test_deleted_nodes_free_quota_and_hide_from_dedup_and_render(tmp_path):
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute("derive class widget from entity with {} quota 1")
    s.execute('assert widget {name: "Ionix"} as a')
    a = s.store.by_name[("widget", "ionix")][0]
    s.store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    out = s.execute('assert widget {name: "Ionix Co"} as b')  # quota freed, no dup offered
    assert "dup candidates" not in out
    assert "widget" in s.schema.render(s.store)
    b = s.store.by_name[("widget", "ionix co")][0]
    s.store.apply({"op": "delete", "id": b, "kind": "node", "reason": "", "cascade": [["node", b]]})
    assert "widget" not in s.schema.render(s.store)
    s.close()


def test_sweep_skips_a_tombstoned_node(tmp_path):
    """The async sweep is a reader too: a deleted node must never come back
    as a duplicate candidate, cross-block match or not."""
    from theorem.engine import dedup

    store = Store(tmp_path)
    a = put(store, "supplier", name="VoltaChem", country="DE")
    # "The VoltaChem" blocks under "thev", a different block than "volt",
    # so only the cross-block sweep (not sync_candidates) would catch this pair.
    put(store, "supplier", name="The VoltaChem", country="DE")
    store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    assert dedup.sweep(store) == 0
    assert store.dup_ledger == []
