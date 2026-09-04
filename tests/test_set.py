from theorem.canonical import canonical
from theorem.parser import parse
from theorem.schema import Schema
from theorem.session import Session


def test_set_parses_none_as_removal():
    stmt = parse('set #s-1 {country: none, name: "B"}')[0]
    assert stmt.props == {"country": None, "name": "B"}
    assert canonical("set #s-1 {country: none}") == "set #s-1 {country: none}"


def test_set_changes_and_removes_values(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A", country: "DE"} as a')
    out = s.execute("set a {country: none}")
    assert "receipt: set #s-1" in out
    assert s.rows("find supplier as x\nreturn x.name, x.country") == [["A", None]]
    s.execute('set #s-1 {country: "KR"}')
    assert s.rows("find supplier as x\nreturn x.country") == [["KR"]]
    s.close()


def test_set_is_verified_like_assert(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} as a')
    out = s.run('set a {colour: "red"}')
    assert "unknown property" in out and "nothing was executed" in out
    out = s.run("set a {country: 5}")
    assert "error" in out and "nothing was executed" in out
    out = s.run("set a {name: none}")
    assert "name" in out and "nothing was executed" in out
    s.close()


def test_set_by_id_is_typed_too(tmp_path):
    s = Session(tmp_path / "db", Schema.supply_chain())
    s.execute('assert supplier {name: "A"} as a')
    out = s.run("set #s-1 {country: 5}")
    assert "error" in out and "country" in out
    assert s.store.nodes["#s-1"].props.get("country") is None
    s.close()
