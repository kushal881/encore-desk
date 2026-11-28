"""Issue #45: a drawing whose finishes still say NAME + CODE (placeholders from the approved template) cannot be issued or
re-issued; the missing finish becomes a question on Specify. Old issued drawings are not changed; an AI change waiting
still blocks first. The drawing part needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, finishcheck as fc

A = "/orders/Q-2026-10-444"
c = TestClient(app)
LAM = {"code": "LAMINATE NAME + CODE", "use": "Table top laminate"}
FAB = {"code": "FABRIC NAME + CODE / WHITE WRITING SHEET", "use": "Pin-up screen fabric"}


def test_rules():
    assert fc.missing({"swatches": [LAM, {"code": "BLACK PVC EDGEBAND", "use": "Table top edge"}, FAB]}) == ["laminate", "fabric"]
    assert fc.missing({"swatches": [{"code": "LAUREL OAK DARK 3416", "use": "Table top laminate"}]}) == []
    assert fc.missing({"swatches": [{"code": "TBD", "use": "Carcass"}, {"code": "[shade]", "use": "Shutter laminate"}]}) == ["laminate"]
    assert fc.missing({"swatches": [{"code": "", "use": "MS frame"}]}) == ["ms frame"]                 # empty value
    assert fc.missing(None) == [] and fc.missing({}) == [] and fc.missing({"swatches": None}) == []       # missing value
    assert fc.reason({"swatches": [LAM, FAB]}) == "finishes not set (laminate, fabric)" and fc.reason({}) == ""
    q = fc.questions({"swatches": [LAM, FAB]}, {"fabric": "Festival F157"})
    assert [x["key"] for x in q] == ["laminate"]                                                         # the line gives the fabric
    assert fc.questions({"swatches": [LAM]}, {}, have={"laminate"}) == []


class _L:
    pass


def _line(no):
    """One product with its drawing, read in full (usable after the session closes)."""
    with db.Session() as s:
        l = s.query(db.OrderLine).filter_by(no=no).one()
        out = _L(); out.id = l.id; out.drawing = _L()
        out.drawing.spec = l.drawing.spec if l.drawing else {}
        return out


def _drop_finishes(no):
    """The quotation line without its Laminate / Fabric lines, as on the Waaree staging order."""
    with db.Session() as s:
        l = s.query(db.OrderLine).filter_by(no=no).one()
        l.description = "\n".join(x for x in l.description.splitlines() if not x.lower().startswith(("laminate", "fabric")))
        s.commit()


def test_issue_blocked_until_finishes_set():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    _drop_finishes(1)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    d = _line(1).drawing
    assert fc.missing(d.spec) == ["laminate", "fabric"]                       # the preview may show placeholders

    # the missing finishes are now questions on Specify, and Issue names the product and what is missing
    v = c.get(A + "/issue").text
    assert "Product 1" in v and "finishes not set (laminate, fabric). Answer them on Specify before issuing" in v
    with db.Session() as s:
        keys = [q.key for q in s.query(db.Question).filter_by(line_id=_line(1).id)]
    assert "laminate" in keys and "fabric" in keys
    assert "The quotation line gives no laminate" in c.get(A + "/specify/1").text
    r = c.post(A + "/issue", data={"folder_no": "6", "nos": "1", "back": "issue"})
    assert r.status_code == 400 and "Product 1: finishes not set (laminate, fabric). Answer them on Specify before issuing." in r.text
    with db.Session() as s:
        assert s.query(db.Revision).count() == 0

    # a filled finish issues normally (product 3 has its laminate on the line)
    assert c.post(A + "/issue", data={"folder_no": "6", "nos": "3", "back": "issue"}, follow_redirects=False).status_code == 303

    # answered but the preview not drawn again: still refused, with what to do
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).filter_by(line_id=_line(1).id)}
    c.post("%s/answer/%d" % (A, qs["laminate"]), data={"value": "Laurel Oak Dark 3416"})
    c.post("%s/answer/%d" % (A, qs["fabric"]), data={"value": "Festival F157"})
    assert "Draw the preview again" in c.post(A + "/issue", data={"folder_no": "6", "nos": "1", "back": "issue"}).text
    c.post(A + "/draw", data={"no": 1})
    sp = _line(1).drawing.spec
    assert fc.missing(sp) == [] and any(sw["code"] == "LAUREL OAK DARK 3416" for sw in sp["swatches"])
    assert c.post(A + "/issue", data={"folder_no": "6", "nos": "1", "back": "issue"}, follow_redirects=False).status_code == 303

    # an AI change waiting still blocks first, before the finishes
    with db.Session() as s:
        l2 = s.query(db.OrderLine).filter_by(no=2).one()
        s.add(db.AiTurn(line_id=l2.id, typed="make it 1500", kind="change", status="trial")); s.commit()
    _drop_finishes(2)
    with db.Session() as s:                                                   # its preview with placeholders
        d2 = s.query(db.Drawing).filter_by(line_id=l2.id).one()
        d2.spec = dict(d2.spec, swatches=[LAM]); s.commit()
    r = c.post(A + "/issue", data={"folder_no": "6", "nos": "2", "back": "issue"})
    assert r.status_code == 400 and "Accept or Undo the change first" in r.text and "finishes" not in r.text


def test_old_issued_drawings_with_placeholders_unchanged():
    """Old data: a drawing issued before this check, with placeholders, stays as issued; it is not re-checked or changed."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:
        r = s.query(db.Revision).join(db.OrderLine).filter(db.OrderLine.no == 3).one()
        r.spec = dict(r.spec, swatches=[LAM]); rid = r.id
        d = s.query(db.Drawing).filter_by(line_id=r.line_id).one()
        d.spec = dict(d.spec, swatches=[LAM])
        s.commit()
    v = c.get(A + "/issue").text
    assert "Product 3: finishes not set" not in v
    with db.Session() as s:
        assert s.get(db.Revision, rid).spec["swatches"] == [LAM] and s.query(db.Revision).filter_by(line_id=r.line_id).count() == 1
    # an approved (locked) drawing is not offered again and not blocked by the check
    c.post("%s/answer-from-client/%d" % (A, rid), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "Called"})
    v = c.get(A + "/issue").text
    assert "Product 3: finishes not set" not in v


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
