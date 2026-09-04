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
