from theorem.engine.storage import Store


def put(store, cls, support=None, **props):
    nid = store.next_id(cls)
    rec = {"op": "put_node", "id": nid, "cls": cls, "props": props}
    if support is not None:
        rec["support"] = support
    store.apply(rec)
    return nid


def link(store, etype, support=None, **roles):
    eid = store.next_id("edge")
    rec = {"op": "put_edge", "id": eid, "type": etype, "roles": roles}
    if support is not None:
        rec["support"] = support
    store.apply(rec)
    return eid


def test_support_is_stored_and_indexed_both_ways(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"], ["doc:b.csv#r7"]], name="Ionix")
    assert store.nodes[a].support == [["doc:a.csv#r1"], ["doc:b.csv#r7"]]
    assert store.dependents["doc:a.csv#r1"] == {a}
    assert store.dependents["doc:b.csv#r7"] == {a}
    assert store.sources["doc:a.csv"] == {a}
    assert store.sources["doc:b.csv"] == {a}


def test_an_artifact_id_in_a_bundle_is_a_dependency(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="Ionix")
    z = put(store, "supplier", support=[[a]], name="Ionix Group")
    assert store.dependents[a] == {z}


def test_edges_depend_on_their_endpoints(tmp_path):
    store = Store(tmp_path)
    p = put(store, "part", name="cell")
    s = put(store, "supplier", name="Ionix")
    e = link(store, "supplied_by", support=[["doc:a.csv#r1"]], item=p, source=s)
    assert e in store.dependents[p] and e in store.dependents[s]
    assert store.edge_index[e].support == [["doc:a.csv#r1"]]
    assert store.sources["doc:a.csv"] == {e}


def test_a_record_without_support_is_self_supported(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="Ionix")
    assert store.nodes[a].support == []
    assert store.nodes[a].live is True


def test_support_and_tombstones_survive_a_snapshot_and_reopen(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", support=[["doc:a.csv#r1"]], name="Ionix")
    store.nodes[a].deleted_at = 3
    store.nodes[a].deleted_by = 3
    store.snapshot()
    store.close()
    again = Store(tmp_path)
    assert again.nodes[a].support == [["doc:a.csv#r1"]]
    assert again.nodes[a].deleted_at == 3
    assert again.nodes[a].deleted_by == 3
    assert again.nodes[a].live is False
    assert again.dependents["doc:a.csv#r1"] == {a}


def test_a_receipt_keeps_its_eight_smallest_bundles(tmp_path):
    store = Store(tmp_path)
    bundles = [[f"doc:{i}.csv#r1"] * (10 - i) for i in range(10)]  # sizes 10 down to 1
    a = put(store, "supplier", support=bundles, name="A")
    kept = store.nodes[a].support
    assert len(kept) == 8 and [len(b) for b in kept] == list(range(1, 9))


from theorem.canonical import canonical
from theorem.parser import parse
from theorem.schema import Schema
from theorem.session import Session


def test_support_parses_and_canonicalizes():
    prog = 'assert supplier {name: "A"} support [doc:a.csv#r1, #s-9] or [doc:b.csv#r2] as a'
    stmt = parse(prog)[0]
    assert stmt.support == [["doc:a.csv#r1", "#s-9"], ["doc:b.csv#r2"]]
    assert canonical(prog) == prog


def test_source_is_shorthand_for_one_bundle():
    a = parse('assert supplier {name: "A"} source doc:a.csv#r1 as a')[0]
    b = parse('assert supplier {name: "A"} support [doc:a.csv#r1] as a')[0]
    assert a == b
    assert canonical('assert supplier {name: "A"} source doc:a.csv#r1 as a') == (
        'assert supplier {name: "A"} support [doc:a.csv#r1] as a'
    )


def test_support_lands_on_the_node_and_edge(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} support [doc:a.csv#r1] as a')
    s.execute('assert part {name: "cell"} as p')
    s.execute("assert edge supplied_by(item: p, source: a) support [doc:a.csv#r1]")
    a = s.store.by_name[("supplier", "a")][0]
    assert s.store.nodes[a].support == [["doc:a.csv#r1"]]
    assert s.store.nodes[a].props["_source"] == "doc:a.csv#r1"
    (edge,) = [e for e in s.store.edge_index.values()]
    assert edge.support == [["doc:a.csv#r1"]]
    s.close()


def test_a_bundle_naming_a_dead_or_unknown_node_is_refused(tmp_path):
    import pytest

    from theorem.engine.writes import WriteError

    s = Session(tmp_path / "db", Schema.supply_chain())
    with pytest.raises(WriteError):
        s.execute('assert supplier {name: "A"} support [#s-77] as a')
    s.close()


def test_merge_survivor_inherits_both_receipts(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} support [doc:a.csv#r1] as a')
    s.execute('assert supplier {name: "Ionix Co"} support [doc:b.csv#r2] as b')
    s.execute("merge a, b prefer newest")
    survivor = s.store.resolve(s.store.by_name[("supplier", "ionix co")][0])
    assert s.store.nodes[survivor].support == [["doc:a.csv#r1"], ["doc:b.csv#r2"]]
    s.close()


def test_compact_summary_cites_its_members_by_default(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} as a')
    s.execute('assert supplier {name: "B"} as b')
    s.execute('find supplier as all\ncompact all as summary {name: "AB"}')
    summary = s.store.by_name[("supplier", "ab")][0]
    members = sorted(s.store.nodes[summary].support[0])
    assert members == sorted(s.store.by_name[("supplier", "a")] + s.store.by_name[("supplier", "b")])
    s.close()


def test_merge_rehomed_edges_keep_their_receipts(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} as a')
    s.execute('assert supplier {name: "Ionix Co"} as b')
    s.execute('assert part {name: "cell"} as p')
    s.execute("assert edge supplied_by(item: p, source: b) support [doc:a.csv#r3]")
    s.execute("merge a, b prefer newest")
    live = [e for e in s.store.edge_index.values() if e.live]
    assert len(live) == 1 and live[0].support == [["doc:a.csv#r3"]]
    s.close()


def test_merge_does_not_resurrect_a_deleted_edge(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "Ionix"} as a')
    s.execute('assert supplier {name: "Ionix Co"} as b')
    s.execute('assert part {name: "cell"} as p')
    s.execute("assert edge supplied_by(item: p, source: b) support [doc:a.csv#r3]")
    (eid,) = [e.id for e in s.store.edge_index.values() if e.live]
    s.store.apply(
        {
            "op": "delete",
            "id": eid,
            "kind": "edge",
            "reason": "",
            "cascade": [["edge", eid]],
        }
    )
    s.execute("merge a, b prefer newest")
    live_supplied_by = [
        e for e in s.store.edge_index.values() if e.type == "supplied_by" and e.live
    ]
    assert live_supplied_by == []
    s.close()
