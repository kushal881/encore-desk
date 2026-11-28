"""Making production / subcontractor drawings (screen 11): one set per product, every time. Made again for the same
revision it keeps its P number, its release and the construction changes, and the record kept in the storage bucket is
that same set. Covers old data (sets from earlier desk versions, incl. duplicate rows), an AI change still waiting, a
released (locked) set, and empty values. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, storage

c = TestClient(app)
A = "/orders/Q-2026-10-444"
P = A + "/production/3"
KUSHAL, SUSHIL = 1, 2


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def sets(kind="PRODUCTION"):
    """-> {line no: [sets of that product, oldest first]}"""
    with db.Session() as s:
        out = {}
        for ps, no in s.query(db.ProductionSet, db.OrderLine.no).join(db.OrderLine, db.OrderLine.id == db.ProductionSet.line_id) \
                       .filter(db.ProductionSet.kind == kind).order_by(db.ProductionSet.id):
            out.setdefault(no, []).append(ps)
        return out


def one_each(kind="PRODUCTION"):
    """Exactly one set per product -> {line no: set}"""
    got = sets(kind)
    assert got and all(len(v) == 1 for v in got.values()), {k: [x.id for x in v] for k, v in got.items()}
    return {k: v[0] for k, v in got.items()}


def test_make_sets(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    kept = []                                                          # what went to the storage bucket
    monkeypatch.setattr(storage, "keep", lambda path, key: kept.append((path, key)) or key)
    as_(SUSHIL)
    c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        o.delivery_date = "10 DEC 2026"
        s.add(db.Step(order_id=o.id, kind="approved", note="Mr Singh, by email", by="Sushil Singhal")); s.commit()
    as_(KUSHAL); c.post(A + "/step/released", data={"note": ""}); as_(SUSHIL)

    # empty values: no job order, date or deadline typed -> TBC / the Odoo delivery date; one set per product
    c.post(A + "/sets/production", data={"job_order": " ", "job_order_date": "", "deadline": ""})
    first = one_each()
    ps = first[3]
    assert ps.prod_no == 1 and not ps.released_at and ps.spec and ps.spec["drawing_set"] == "PRODUCTION"
    assert ps.details["deadline"] == "10 DEC 2026" and ps.details["job_order_date"] == "TBC" and ps.details["job_order"]
    assert ps.spec["drawing"].get("approved") == "CLIENT"                                  # the recorded client approval
    # the set kept in the bucket is the set on record (its id names the folder entry), not a second copy
    assert kept and all(any(("/%d-" % x.id) in k for _, k in kept) for x in first.values())
    assert {k.split("/Other/Production/")[1].split("-")[0] for _, k in kept} == {str(x.id) for x in first.values()}

    # an AI change still waiting (Accept / Undo not pressed) when the drawings are made again
    with db.Session() as s:
        pc = db.ProdChange(line_id=ps.line_id, set_id=ps.id, typed="use 350 channels", status="trial", spec=dict(ps.spec), files=ps.files)
        s.add(pc); s.commit(); pcid = pc.id
    c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0119"})
    ps = one_each()[3]
    assert ps.prod_no == 1 and ps.details["job_order"] == "JO 26-27-11-0119" and ps.spec
    h = c.post(P + "/ai/%d/accept" % pcid).text                          # the waiting change was on the old set: ask again
    assert "The production drawing changed since this was proposed" in h
    with db.Session() as s:
        assert s.get(db.ProdChange, pcid).status == "undone"
    assert len(sets()[3]) == 1

    # approved / locked: released P1 made again for the same revision keeps P1, its release and its specification
    with db.Session() as s:                                            # a construction change already accepted into P1
        x = s.get(db.ProductionSet, ps.id); x.spec = dict(x.spec, model=dict(x.spec["model"], slide_len=350)); s.commit()
    assert c.post(P + "/release").status_code in (200, 303)
    rel = one_each()[3]
    assert rel.released_by == "Sushil Singhal" and rel.released_at
    kept.clear()
    c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0120"})
    again = one_each()
    ps = again[3]
    assert ps.id != rel.id and ps.prod_no == 1 and ps.released_by == "Sushil Singhal" and ps.released_at
    assert ps.spec["model"]["slide_len"] == 350 and ps.details["job_order"] == "JO 26-27-11-0120"
    assert {k.split("/Other/Production/")[1].split("-")[0] for _, k in kept} == {str(x.id) for x in again.values()}
    assert "P1 released by Sushil Singhal" in c.get(P).text
    assert c.post(P + "/release").status_code == 400                                       # still released, not a fresh P1

    # subcontractor drawings: one VENDOR set per product, the production sets left as they are
    c.post(A + "/sets/vendor", data={"deadline": "05 DEC 2026"})
    c.post(A + "/sets/vendor", data={"deadline": "06 DEC 2026"})
    v = one_each("VENDOR")
    assert v[3].details["deadline"] == "06 DEC 2026" and v[3].spec and v[3].prod_no == 1
    assert {k: x.id for k, x in one_each().items()} == {k: x.id for k, x in again.items()}

    # old data: duplicate rows left by an earlier desk version (one without a specification, P number or release)
    with db.Session() as s:
        cur = s.get(db.ProductionSet, ps.id)
        s.add(db.ProductionSet(line_id=cur.line_id, revision_id=cur.revision_id, kind="PRODUCTION", files=cur.files, details=cur.details,
                               engine_version="approval 3 (01 Oct 2026)", made_by="old desk"))
        s.add(db.ProductionSet(line_id=cur.line_id, revision_id=cur.revision_id, kind="PRODUCTION", files=cur.files, details=cur.details,
                               engine_version="approval 3 (01 Oct 2026)", made_by="old desk", prod_no=2, spec={},
                               released_by="Kushal Singhal", released_at=cur.released_at))
        s.commit()
    assert len(sets()[3]) == 3 and c.get(P).status_code == 200
    c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0121"})
    ps = one_each()[3]
    with db.Session() as s:
        r = s.get(db.Revision, ps.revision_id)
    # the newest old row wins: its P number and release are kept; it kept no specification, so the revision's is used
    assert ps.prod_no == 2 and ps.released_by == "Kushal Singhal" and ps.released_at
    assert ps.spec["model"] == r.spec["model"] and ps.spec["drawing_set"] == "PRODUCTION"
    assert "production drawing P2" in c.get(P).text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
