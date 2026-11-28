"""Screen 2: New project and Remake. Open quotations only, server folder number (checked, kept on the client, no clash
with another client this month), and remake of client-approved drawings into a project of an open quotation.
Drawing parts need ENGINES_DIR."""
import os, sys, tempfile, datetime
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, newproject as np

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def test_folder_rules():
    assert np.clean_folder_no("5") == "05" and np.clean_folder_no(" 12 ") == "12"
    assert np.clean_folder_no("") is None and np.clean_folder_no("abc") is None and np.clean_folder_no("0") is None and np.clean_folder_no("100") is None
    assert np.folder_name("Fault Code Design", "05", datetime.date(2026, 11, 28)) == "Encore-Nov-2026-05-Fault Code Design"
    assert np.folder_name("A/B: Co", "07", datetime.date(2026, 11, 28)) == "Encore-Nov-2026-07-A B Co"     # no characters the server refuses


def test_new_project():
    assert c.get("/new", follow_redirects=False).headers["location"] == "/login"
    c.post("/login/dev/2")
    h = c.get("/new").text
    assert "Start a new drawing project" in h and "Q-2026-10-444" in h and "SAMPLE-REPEAT-1" in h
    assert "No client-approved drawings yet." in h
    # empty or wrong folder number: refused, nothing made
    for f in ("", "abc", "0"):
        h = c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": f}).text
        assert "Enter the server folder number (1 to 99)" in h
    assert "Pick a quotation first" in c.post("/new", data={"ref": "", "folder_no": "6"}).text
    assert "was not found" in c.post("/new", data={"ref": "Q-NOPE", "folder_no": "6"}).text
    # another client holds folder 06 this month: refused
    with db.Session() as s:
        other = db.Client(name="Fault Code Design", folder_no="06")
        s.add(db.Order(client=other, source="odoo", source_ref="Q-OTHER-1")); s.commit()
    assert "Folder 06 is already used this month by Fault Code Design" in c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": "6"}).text
    r = c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": "5"}, follow_redirects=False)
    assert r.headers["location"] == A
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        assert o.client.folder_no == "05" and len(o.lines) == 3
        assert any("Encore-%s-%d-05-Achiever Apparels Pvt Ltd" % (datetime.date.today().strftime("%b"), datetime.date.today().year) in m.text for m in o.messages)
    h = c.get("/new").text
    assert 'name="ref" value="Q-2026-10-444"' not in h and 'name="ref" value="SAMPLE-REPEAT-1"' in h   # a project now: not offered as new
    # the same client keeps its folder number: another number is refused, an empty one takes 05 (tested in test_remake)
    assert "already has folder 05" in c.post("/new", data={"ref": "SAMPLE-REPEAT-1", "folder_no": "9"}).text
    # opening an existing project again needs no folder number
    assert c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": ""}, follow_redirects=False).headers["location"] == A


def test_remake():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    # an AI change left waiting on product 3 keeps it from issue; 1 and 2 are issued
    with db.Session() as s:
        l3 = s.query(db.OrderLine).filter_by(no=3).one()
        s.add(db.AiTurn(line_id=l3.id, typed="make it 600 deep", kind="change", status="trial")); s.commit()
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    with db.Session() as s:
        revs = {s.get(db.OrderLine, r.line_id).no: r for r in s.query(db.Revision)}
    assert set(revs) == {1, 2}
    assert "No client-approved drawings yet." in c.get("/new").text                # issued is not approved
    c.post("%s/answer-from-client/%d" % (A, revs[2].id), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "approved on call"})
    h = c.get("/new").text
    assert revs[2].number in h and revs[1].number not in h and "Client approved" in h
    assert revs[2].number in c.get("/new?p=storage").text and revs[2].number not in c.get("/new?p=zzz").text
    # nothing ticked / not approved: refused
    assert "Tick at least one past drawing" in c.post("/new/remake", data={"ref": "SAMPLE-REPEAT-1"}).text
    assert "Only client-approved drawings can be made again" in c.post("/new/remake", data={"ref": "SAMPLE-REPEAT-1", "rid": str(revs[1].id)}).text
    # remake the approved storage into SAMPLE-REPEAT-1: its own "Storage ESP 01" product takes the copy
    with db.Session() as s:
        assert s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").count() == 0         # refused: no project was made
    c.post("/new/remake", data={"ref": "SAMPLE-REPEAT-1", "rid": str(revs[2].id)})       # no folder number: the client's 05
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        assert o.client.folder_no == "05"
        l1 = next(l for l in o.lines if l.no == 1)
        ch = [c_ for c_ in l1.changes if "remake_of" in (c_.values or {})]
        assert ch and ch[0].values["remake_of"] == revs[2].id and ch[0].status == "confirmed"
        assert l1.drawing is not None and l1.drawing.status == "preview" and not o.added
    # a second copy into the same project: no free matching product, so it is added and the quotation must be updated
    c.post("/new/remake", data={"ref": "SAMPLE-REPEAT-1", "rid": str(revs[2].id)})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        assert [a.no for a in o.added] == [3] and next(l for l in o.lines if l.no == 3).drawing.status == "preview"
    # old data: an order approved as a whole (a step, before approval records were kept) counts as client-approved
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Step(order_id=o.id, kind="approved", note="Mr Singh, by email", by="Sushil Singhal")); s.commit()
    assert revs[1].number in c.get("/new").text
    v = c.get("/orders/SAMPLE-REPEAT-1/view/1").text
    assert "exact remake" in v or "remake of a past drawing" in v


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
