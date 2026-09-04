import pytest

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


def test_patch_edge_without_support_is_refused_before_the_wal(tmp_path):
    import pytest

    from theorem.engine.storage import StoreError

    store = Store(tmp_path)
    p = put(store, "part", name="cell")
    s = put(store, "supplier", name="Ionix")
    e = link(store, "supplied_by", support=[["doc:a.csv#r1"]], item=p, source=s)
    before = store.position
    with pytest.raises(StoreError):
        store.apply({"op": "patch_edge", "id": e})
    assert store.position == before
    assert store.wal_len() == before
    store.close()
    again = Store(tmp_path)
    assert again.edge_index[e].support == [["doc:a.csv#r1"]]
    again.close()


def test_restore_refuses_malformed_kept_dead(tmp_path):
    import pytest

    from theorem.engine.storage import StoreError

    store = Store(tmp_path)
    a = put(store, "supplier", name="A")
    p = store.apply({"op": "delete", "id": a, "kind": "node", "reason": "", "cascade": [["node", a]]})
    with pytest.raises(StoreError):
        store.apply({"op": "restore", "of": p, "kept_dead": [["node", a]]})


def _session(tmp_path):
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} support [doc:a.csv#r1] as a')
    s.execute('assert part {name: "cell"} support [doc:a.csv#r2] as p')
    s.execute("assert edge supplied_by(item: p, source: a) support [doc:a.csv#r2]")
    return s


def test_delete_parses_and_canonicalizes():
    from theorem.canonical import canonical
    from theorem.parser import parse

    stmt = parse('delete #s-1 reason "dup" preview')[0]
    assert (stmt.ref, stmt.kind, stmt.reason, stmt.preview) == ("#s-1", "node", "dup", True)
    assert canonical('delete edge #e-3 reason "wrong"') == 'delete edge #e-3 reason "wrong"'


def test_preview_writes_nothing_and_matches_commit(tmp_path):
    s = _session(tmp_path)
    before = s.store.position
    out = s.execute('delete #s-1 reason "gone" preview')
    assert "preview: delete #s-1 would remove" in out
    assert "cascade: 1 nodes, 1 edges" in out
    assert s.store.position == before
    out = s.execute('delete #s-1 reason "gone"')
    assert "receipt: deleted #s-1 at @t-" in out
    assert "cascade: 1 nodes, 1 edges" in out
    assert "restore with: restore @t-" in out
    assert s.rows("find supplier as x\nreturn x.name") == []
    s.close()


def test_delete_by_binding_name(tmp_path):
    s = _session(tmp_path)
    out = s.execute('find supplier where name = "Ionix" as x\ndelete x reason "gone"')
    assert "receipt: deleted #s-1" in out
    s.close()


def test_delete_edge(tmp_path):
    s = _session(tmp_path)
    out = s.execute('delete edge #e-1 reason "wrong link"')
    assert "receipt: deleted #e-1" in out
    assert s.rows("find part as p\nfollow p supplied_by source as sup\nreturn sup.name") == []
    assert s.rows("find supplier as x\nreturn x.name") == [["Ionix"]]
    s.close()


def test_delete_of_a_deleted_node_is_an_error(tmp_path):
    s = _session(tmp_path)
    s.execute('delete #s-1 reason "gone"')
    out = s.run('delete #s-1 reason "again"')
    assert "error" in out and "#s-1" in out
    s.close()


def test_withdraw_takes_everything_only_that_source_built(tmp_path):
    s = _session(tmp_path)
    s.execute('assert supplier {name: "Keep"} support [doc:a.csv#r9] or [doc:b.csv#r1] as k')
    out = s.execute('withdraw doc:a.csv reason "retracted" preview')
    assert "preview: withdraw doc:a.csv would remove" in out
    assert "cascade: 2 nodes, 1 edges" in out
    out = s.execute('withdraw doc:a.csv reason "retracted"')
    assert "receipt: withdrew doc:a.csv at @t-" in out
    assert s.rows("find supplier as x\nreturn x.name") == [["Keep"]]
    s.close()


def test_withdraw_accepts_an_anchor_and_uses_the_document(tmp_path):
    from theorem.parser import parse

    assert parse('withdraw doc:a.csv#r3 reason "x"')[0].source == "doc:a.csv"


def test_restore_reverses_a_whole_cascade(tmp_path):
    s = _session(tmp_path)
    out = s.execute('delete #s-1 reason "oops"')
    pos = out.split("restore @t-")[1].split()[0]
    out = s.execute(f"restore @t-{pos}")
    assert "receipt: restored 1 nodes, 1 edges from @t-" in out
    assert s.rows("find part as p\nfollow p supplied_by source as sup\nreturn sup.name") == [["Ionix"]]
    s.close()


def test_restore_of_a_position_that_deleted_nothing_is_an_error(tmp_path):
    s = _session(tmp_path)
    out = s.run("restore @t-1")
    assert "error" in out and "nothing was deleted at @t-1" in out
    s.close()


def test_restore_leaves_an_artifact_dead_when_its_other_evidence_is(tmp_path):
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} as a\nassert supplier {name: "B"} as b')
    s.execute('assert supplier {name: "Z"} support [#s-1, #s-2] as z')
    first = s.execute('delete #s-1 reason "a"').split("restore @t-")[1].split()[0]
    second = s.execute('delete #s-2 reason "b"').split("restore @t-")[1].split()[0]
    out = s.execute(f"restore @t-{first}")
    assert "restored 1 nodes, 0 edges" in out and "still dead: 1" in out and f"needs restore @t-{second}" in out
    assert s.rows("find supplier as x\nreturn x.name order by x.name") == [["A"]]
    s.execute(f"restore @t-{second}")
    assert s.rows("find supplier as x\nreturn x.name order by x.name") == [["A"], ["B"], ["Z"]]
    s.close()


def test_restore_refuses_a_drop(tmp_path):
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute("derive class tag from entity with {}")
    out = s.execute('drop class tag reason "x"')
    pos = out.split("at @t-")[1].split()[0]
    assert "derive it again" in s.run(f"restore @t-{pos}")
    s.close()


def test_restore_of_a_withdrawal_attributes_a_still_dead_artifact_to_the_other_withdrawal(tmp_path):
    """Z's evidence is two provenance tokens in one bundle, no node ids. When
    both sources are withdrawn and the older withdrawal is restored, Z has no
    dead #-id evidence to blame; the guard must fall back to the newest
    withdrawal whose source Z's support still cites."""
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Z"} support [doc:a.csv#r1, doc:b.csv#r1] as z')
    first = s.execute('withdraw doc:a.csv reason "a"').split("restore @t-")[1].split()[0]
    second = s.execute('withdraw doc:b.csv reason "b"').split("restore @t-")[1].split()[0]
    out = s.execute(f"restore @t-{first}")
    assert "restored 0 nodes, 0 edges" in out
    assert "still dead: 1" in out and f"needs restore @t-{second}" in out
    assert s.rows("find supplier as x\nreturn x.name") == []
    s.execute(f"restore @t-{second}")
    assert s.rows("find supplier as x\nreturn x.name") == [["Z"]]
    s.close()


def test_a_withdrawn_source_is_dead_evidence_whichever_statement_ran_first(tmp_path):
    """Liveness is a property of the graph, not of the order two statements
    ran in. Z rests on a withdrawn document or on a deleted node; once both
    are gone it is dead either way round."""
    withdraw_first = Store(tmp_path / "a")
    s = put(withdraw_first, "supplier", name="S")
    z = put(withdraw_first, "supplier", support=[["doc:a.csv#r1"], [s]], name="Z")
    withdraw_first.apply(
        {"op": "withdraw", "source": "doc:a.csv", "reason": "", "cascade": []}
    )
    assert withdraw_first.nodes[z].live is True  # the bundle citing S still holds
    assert withdraw_first.cascade(nodes=[s]).nodes == [s, z]

    delete_first = Store(tmp_path / "b")
    s = put(delete_first, "supplier", name="S")
    z = put(delete_first, "supplier", support=[["doc:a.csv#r1"], [s]], name="Z")
    delete_first.apply(
        {"op": "delete", "id": s, "kind": "node", "reason": "", "cascade": [["node", s]]}
    )
    assert delete_first.nodes[z].live is True
    assert delete_first.cascade(sources=["doc:a.csv"]).nodes == [z]


def test_restore_blames_the_deletion_that_still_holds_an_edge_endpoint(tmp_path):
    """An edge kept dead by a still-deleted endpoint must point the operator
    at the deletion that took the endpoint, not at the restore just run."""
    from theorem.schema import Schema
    from theorem.session import Session

    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} as a')
    s.execute('assert part {name: "cell"} as p')
    s.execute("assert edge supplied_by(item: p, source: a)")
    first = s.execute('delete #s-1 reason "supplier"').split("restore @t-")[1].split()[0]
    second = s.execute('delete #p-1 reason "part"').split("restore @t-")[1].split()[0]
    out = s.execute(f"restore @t-{first}")
    assert "still dead: 1" in out
    assert f"#e-1 (needs restore @t-{second})" in out
    assert f"needs restore @t-{first}" not in out
    s.execute(f"restore @t-{second}")
    rows = s.rows("find part as p\nfollow p supplied_by source as sup\nreturn sup.name")
    assert rows == [["Ionix"]]
    s.close()


def test_withdraw_parses_and_canonicalizes():
    from theorem.canonical import canonical
    from theorem.parser import parse

    stmt = parse('withdraw doc:a.csv reason "dup" preview')[0]
    assert (stmt.source, stmt.reason, stmt.preview) == ("doc:a.csv", "dup", True)
    assert canonical('restore @t-7') == 'restore @t-7'
    assert canonical('withdraw doc:a.csv reason "retracted"') == 'withdraw doc:a.csv reason "retracted"'
