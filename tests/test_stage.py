from pathlib import Path

import pytest

from theorem.ingest.chunk import split
from theorem.ingest.normalize import normalize
from theorem.ingest.stage import stage
from theorem.schema import Schema
from theorem.session import Session


@pytest.fixture
def sess(tmp_path):
    return Session(tmp_path / "db", Schema.supply_chain())


def test_split_respects_headings_and_cap():
    body = "# A\n" + ("word " * 700) + "\n# B\nshort"
    chunks = split(body, [])
    assert len(chunks) >= 3  # A splits by cap, B separate
    from theorem.engine.executor import count_tokens

    assert all(count_tokens(t) <= 600 for t, _ in chunks)


def test_stage_document_and_chunks(sess):
    raw = b"# Title\n\nHello graph world.\n\n# Part two\n\nMore text."
    env = normalize(raw, "notes.md")
    r = stage(sess, env, "notes.md", raw)
    assert not r.existing and r.chunks >= 2
    out = sess.run("find chunk as c\nfollow c part_of whole as d\nreturn d.title")
    assert "notes.md" in out


def test_stage_sha_dedup(sess):
    raw = b"# Same\ncontent"
    env = normalize(raw, "a.md")
    r1 = stage(sess, env, "a.md", raw)
    r2 = stage(sess, env, "a.md", raw)
    assert r2.existing and r2.doc_id == r1.doc_id


def test_pdf_pages_prop_matches_page_count(sess):
    pytest.importorskip("pdfplumber")
    raw = (Path(__file__).parent / "fixtures" / "mini.pdf").read_bytes()
    env = normalize(raw, "mini.pdf")
    r = stage(sess, env, "mini.pdf", raw)
    assert sess.store.nodes[r.doc_id].props["pages"] == 1


def test_staged_table_is_refinable(sess):
    raw = b"name,unit_cost\nbolt,1.0\n"
    env = normalize(raw, "parts.csv")
    r = stage(sess, env, "parts.csv", raw)
    out = sess.run(
        f"refine {r.doc_table_ids[0]} into part with "
        f'{{name: col "name", unit_cost: col "unit_cost"}} as np'
    )
    assert "refined" in out


def test_restaging_a_file_whose_document_was_deleted_makes_a_new_one(sess):
    """The sha256 scan is a reader too: a tombstoned document must not
    answer "already staged" for a file nothing in the graph holds."""
    raw = b"# Same\ncontent"
    env = normalize(raw, "a.md")
    r1 = stage(sess, env, "a.md", raw)
    cascade = sess.store.cascade(nodes=[r1.doc_id])
    sess.store.apply(
        {
            "op": "delete",
            "id": r1.doc_id,
            "kind": "node",
            "reason": "superseded",
            "cascade": [["node", n] for n in cascade.nodes]
            + [["edge", e] for e in cascade.edges],
        }
    )
    r2 = stage(sess, env, "a.md", raw)
    assert not r2.existing
    assert r2.doc_id != r1.doc_id
