"""The whole path on the sample order: answer, preview, issue, change, confirm, re-issue. Needs ENGINES_DIR."""
import os, sys, re, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db

c = TestClient(app)
U = "/orders/Q-2026-10-444"


def test_flow():
    h = c.get(U).text
    assert "Draw previews" not in h and "Issue drawings" not in h
    with db.Session() as s:
        qs = {(q.key): q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        assert c.post("%s/answer/%d" % (U, qs[key]), data={"value": val}).status_code == 200
    assert c.post(U + "/issue", data={"folder_no": "06"}).status_code == 400          # no previews yet
    h = c.post(U + "/draw").text
    assert h.count("Preview, not issued") == 3 and "Could not draw" not in h
    assert c.post(U + "/issue", data={"folder_no": "abc"}).status_code == 400
    h = c.post(U + "/issue", data={"folder_no": "6"}).text
    assert h.count("Issued Rev A") == 3 and "Encore-Nov-2026-06-03 Rev A" in h.replace("</b>", "").replace("<b>", "") or "06-03" in h
    assert "Nothing to issue" in c.post(U + "/issue").text
    h = c.post(U + "/change", data={"text": "make it 900 high"}).text
    assert "Which line is this for" in h
    h = c.post(U + "/change", data={"text": "line 1 add a glass top"}).text
    assert "needs an engine change" in h
    h = c.post(U + "/change", data={"text": "Line 2: make it 1500 long with 2 shelves"}).text
    assert "length 1500 mm; 2 shelves in each compartment" in h and "Issue drawings" not in h
    with db.Session() as s:
        cid = s.query(db.Change).filter_by(status="asked").one().id
    h = c.post("%s/change/%d/yes" % (U, cid)).text
    assert "Applied" in h and h.count("Preview, not issued") == 1
    c.post(U + "/issue")
    with db.Session() as s:
        revs = [(r.number, r.rev) for r in s.query(db.Revision).order_by(db.Revision.id)]
        last = s.query(db.Revision).order_by(db.Revision.id.desc()).first()
        assert revs == [("Encore-Nov-2026-06-01", "A"), ("Encore-Nov-2026-06-02", "A"), ("Encore-Nov-2026-06-03", "A"), ("Encore-Nov-2026-06-02", "B")], revs
        assert last.spec["model"]["L"] == 1500 and last.spec["revisions"][-1]["desc"] == "L 1500, shelves 2"
        first = s.query(db.Revision).filter_by(number="Encore-Nov-2026-06-02", rev="A").one()
        assert first.spec["model"]["L"] == 1200 and os.path.exists(first.files["pdf"])     # the earlier revision is untouched
        est = [f.name for f in s.query(db.Finish).filter_by(source="estimate")]
        assert est == ["FESTIVAL F157"], est


if __name__ == "__main__":
    test_flow(); print("flow test passes")
