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


@pytest.mark.xfail(strict=True, reason="alias re-keying lands in Task 3")
def test_a_bundle_citing_an_absorbed_id_follows_the_survivor(tmp_path):
    store = Store(tmp_path)
    a = put(store, "supplier", name="Ionix")
    b = put(store, "supplier", name="Ionix Co")
    z = put(store, "supplier", support=[[b]], name="Z")
    store.apply({"op": "alias", "absorbed": b, "survivor": a})
    store.apply({"op": "retire", "id": b, "reason": f"merged into {a}"})
    assert store.cascade(nodes=[a]).nodes == [a, z]
