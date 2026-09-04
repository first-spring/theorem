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

def test_rename_class_keeps_ids_bases_and_edges(tmp_path):
    from theorem.canonical import canonical

    assert canonical("rename class product to tool") == "rename class product to tool"
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert product {name: "Saw", launch_year: 2024} as p')
    s.execute('assert part {name: "blade"} as b')
    s.execute("assert edge uses(whole: p, component: b)")
    out = s.execute("rename class product to tool")
    assert "receipt: renamed class product to tool" in out
    assert s.rows("find tool as t\nreturn t.name") == [["Saw"]]
    assert s.rows("find part as b\nfollow b uses whole as t\nreturn t.name") == [["Saw"]]
    assert s.store.by_name[("tool", "saw")] == ["#p-1"]
    assert s.run("find product as p\nreturn p.name").startswith("error")
    s.close()


def test_rename_class_is_verified(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    assert "already exists" in s.run("rename class product to part")
    assert "unknown class" in s.run("rename class widget to tool")
    assert "reserved" in s.run("rename class product to nodes")
    assert "lowercase" in s.run("rename class product to Tool")
    s.close()


def test_rename_property(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A", country: "DE"} as a')
    s.execute("rename property supplier.country to nation")
    assert s.rows("find supplier as x\nreturn x.nation") == [["DE"]]
    assert "unknown property" in s.run("find supplier as x\nreturn x.country")
    # supplier declares name itself in the demo schema, so use a subclass to
    # reach an inherited property
    s.execute("derive class vendor from supplier with {tier: int}")
    out = s.run("rename property vendor.nation to region")
    assert "declared on base class supplier" in out
    s.close()


def test_renames_survive_restart(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema.supply_chain())
    s1.execute('assert supplier {name: "A", country: "DE"} as a')
    s1.execute("rename class supplier to vendor\nrename property vendor.country to nation")
    s1.close()
    s2 = Session(db, Schema.supply_chain())
    assert s2.rows("find vendor as v\nreturn v.nation") == [["DE"]]
    s2.close()


def test_alter_add_then_set(tmp_path):
    from theorem.canonical import canonical

    assert canonical("alter class supplier add {rating: float}") == "alter class supplier add {rating: float}"
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} as a')
    s.execute("alter class supplier add {rating: float}\nset a {rating: 4.5}")
    assert s.rows("find supplier as x\nreturn x.rating") == [[4.5]]
    assert "already exists" in s.run("alter class supplier add {name: str}")
    s.close()

def test_alter_drop_removes_values(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A", country: "DE"} as a')
    s.execute("alter class supplier drop {country}")
    assert "unknown property" in s.run("find supplier as x\nreturn x.country")
    assert "country" not in s.store.nodes["#s-1"].props
    # dropping name is rejected before it ever reaches own_prop's base-class
    # check: supplier declares name itself, so the guard is the explicit
    # "nodes are indexed by it" one, not a base-class message
    assert "name cannot be dropped; nodes are indexed by it" in s.run("alter class supplier drop {name}")
    s.close()

def test_alter_retype_converts_or_refuses_naming_offenders(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A", country: "12"} as a')
    s.execute('assert supplier {name: "B", country: "DE"} as b')
    out = s.run("alter class supplier retype {country: int}")
    # unlike a VerifyError, this refusal comes from the writer (it needs the
    # store to preflight every value), so it is a WriteError; as the first
    # and only statement in the program, Session._execute reports zero
    # writes committed, which for a single failing statement means no
    # partial-commit suffix is appended at all
    assert "cannot convert" in out and "#s-2" in out and "committed" not in out
    s.execute('set b {country: "7"}')
    s.execute("alter class supplier retype {country: int}")
    assert s.rows("find supplier as x\nreturn x.name, x.country order by x.name") == [["A", 12], ["B", 7]]
    s.close()

def test_alters_survive_restart(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema.supply_chain())
    s1.execute('assert supplier {name: "A", country: "7"} as a')
    s1.execute("alter class supplier add {rating: float}\nalter class supplier retype {country: int}\nalter class supplier drop {rating}")
    s1.close()
    s2 = Session(db, Schema.supply_chain())
    assert s2.schema.classes["supplier"].props == {"name": "str", "country": "int"}
    assert s2.rows("find supplier as x\nreturn x.country") == [[7]]
    s2.close()

def test_drop_class_takes_nodes_edges_and_edge_types(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert product {name: "Saw"} as p')
    s.execute('assert part {name: "blade"} as b')
    s.execute("assert edge uses(whole: p, component: b)")
    out = s.execute('drop class product reason "not a thing" preview')
    assert "preview: drop class product would remove" in out
    assert "cascade: 1 nodes, 1 edges" in out and "edge types: uses" in out
    assert "product" in s.schema.classes
    out = s.execute('drop class product reason "not a thing"')
    assert "receipt: dropped class product at @t-" in out
    assert "product" not in s.schema.classes and "uses" not in s.schema.edges
    assert "unknown class" in s.run("find product as p\nreturn p.name")
    assert s.rows("find part as b\nreturn b.name") == [["blade"]]
    s.close()

def test_drop_class_refuses_builtins_and_parents(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    assert "built-in" in s.run('drop class entity reason "x"')
    s.execute("derive class gadget from product with {}")
    assert "subclasses" in s.run('drop class product reason "x"')
    s.close()

def test_drop_edge_type(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert product {name: "Saw"} as p')
    s.execute('assert part {name: "blade"} as b')
    s.execute("assert edge uses(whole: p, component: b)")
    out = s.execute('drop edge uses reason "wrong model"')
    assert "receipt: dropped edge uses at @t-" in out and "cascade: 0 nodes, 1 edges" in out
    assert "uses" not in s.schema.edges
    assert s.rows("find product as p\nreturn p.name") == [["Saw"]]
    s.close()

def test_drops_survive_restart_and_a_dropped_name_can_return(tmp_path):
    db = tmp_path / "db"
    s1 = Session(db, Schema())
    s1.execute("derive class tag from entity with {}")
    s1.execute('assert tag {name: "old"} as t')
    s1.execute('drop class tag reason "replace"\nderive class tag from entity with {colour: str}')
    s1.close()
    s2 = Session(db, Schema())
    assert s2.schema.classes["tag"].props == {"colour": "str"}
    assert s2.rows("find tag as t\nreturn t.name") == []
    s2.close()


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "rename_class", "name": "supplier"},
        {"kind": "rename_prop", "cls": "supplier", "name": "country"},
        {"kind": "alter_class", "cls": "supplier"},
        {"kind": "alter_class", "cls": "supplier", "action": "sideways", "decls": {}},
        {"kind": "alter_class", "cls": "supplier", "action": "add"},
        {"kind": "alter_class", "cls": "supplier", "action": "retype"},
        {"kind": "alter_class", "cls": "supplier", "action": "drop"},
        {"kind": "drop_class", "name": "supplier", "cascade": []},
        {"kind": "drop_edge", "name": "supplied_by"},
    ],
)
def test_a_lineage_record_missing_what_replay_reads_is_refused(tmp_path, record):
    """Replay applies these without validating, so a record missing a key
    would raise KeyError inside the constructor and the store would never
    open again. The WAL must never hold one."""
    from theorem.engine.storage import Store, StoreError

    store = Store(tmp_path)
    before = store.position
    with pytest.raises(StoreError):
        store.apply({"op": "lineage", **record})
    assert store.position == before
    assert store.wal_len() == before
    store.close()
