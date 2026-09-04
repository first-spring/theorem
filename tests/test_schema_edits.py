import pytest

from theorem.schema import ClassDef, EdgeDef, Schema
from theorem.session import Session


def test_schema_helpers_rename_class_and_keep_subclasses_and_edges():
    s = Schema.supply_chain()
    s.classes["gadget"] = ClassDef("gadget", {"watts": "int"}, base="product")
    s.rename_class("product", "tool")
    assert "product" not in s.classes and s.classes["tool"].name == "tool"
    assert s.classes["gadget"].base == "tool"
    assert s.edges["uses"].roles == {"whole": "tool", "component": "part"}


def test_schema_helpers_props():
    s = Schema.supply_chain()
    s.rename_prop("supplier", "country", "nation")
    assert list(s.classes["supplier"].props) == ["name", "nation"]
    s.alter_add("supplier", {"rating": "float"})
    s.alter_retype("supplier", {"rating": "int"})
    assert s.classes["supplier"].props["rating"] == "int"
    s.alter_drop("supplier", ["rating"])
    assert "rating" not in s.classes["supplier"].props
    with pytest.raises(KeyError):
        s.rename_prop("supplier", "missing", "x")


def test_schema_helpers_drop():
    s = Schema.supply_chain()
    s.drop_edge("uses")
    assert "uses" not in s.edges
    s.drop_class("product")
    assert "product" not in s.classes


def test_lineage_kinds_replay_in_order_on_reopen(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema())
    s1.execute("derive class product from entity with {price: float}")
    s1.execute('assert product {name: "A", price: 3.5} as a')
    s1.store.apply({"op": "lineage", "kind": "rename_class", "name": "product", "to": "tool"})
    s1.store.apply({"op": "lineage", "kind": "rename_prop", "cls": "tool", "name": "price", "to": "cost"})
    s1.store.apply({"op": "lineage", "kind": "alter_class", "cls": "tool", "action": "add", "decls": {"watts": "int"}})
    s1.store.apply({"op": "lineage", "kind": "alter_class", "cls": "tool", "action": "retype", "decls": {"cost": "str"}})
    s1.store.apply({"op": "lineage", "kind": "alter_class", "cls": "tool", "action": "drop", "names": ["watts"]})
    a = s1.store.by_name[("tool", "a")][0]
    assert s1.store.nodes[a].cls == "tool"
    assert s1.store.nodes[a].props == {"name": "A", "cost": "3.5"}
    s1.close()
    s2 = Session(db, Schema())
    assert "product" not in s2.schema.classes
    assert s2.schema.classes["tool"].props == {"cost": "str"}
    assert s2.store.nodes[a].cls == "tool" and s2.store.nodes[a].props["cost"] == "3.5"
    assert s2.rows("find tool as t\nreturn t.cost") == [["3.5"]]
    s2.close()


def test_a_dropped_class_can_be_derived_again(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema())
    s1.execute("derive class tag from entity with {}")
    s1.store.apply({"op": "lineage", "kind": "drop_class", "name": "tag", "reason": "", "cascade": [], "edge_types": []})
    s1.close()
    s2 = Session(db, Schema())
    assert "tag" not in s2.schema.classes
    s2.execute("derive class tag from entity with {colour: str}")
    s2.close()
    s3 = Session(db, Schema())
    assert s3.schema.classes["tag"].props == {"colour": "str"}
    s3.close()


def test_schema_edits_survive_a_snapshot_then_reopen(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema())
    s1.execute("derive class product from entity with {price: float}")
    s1.execute('assert product {name: "A", price: 3.5} as a')
    s1.store.apply({"op": "lineage", "kind": "rename_class", "name": "product", "to": "tool"})
    s1.store.apply({"op": "lineage", "kind": "alter_class", "cls": "tool", "action": "retype", "decls": {"price": "str"}})
    s1.store.snapshot()
    s1.close()
    s2 = Session(db, Schema())
    assert s2.store.by_class["tool"] == ["#p-1"] and "product" not in s2.store.by_class
    assert s2.store.nodes["#p-1"].props["price"] == "3.5"
    assert s2.rows("find tool as t\nreturn t.price") == [["3.5"]]
    s2.store.snapshot()  # a second snapshot must not change anything either
    s2.close()
    s3 = Session(db, Schema())
    assert s3.rows("find tool as t\nreturn t.price") == [["3.5"]]
    s3.close()
