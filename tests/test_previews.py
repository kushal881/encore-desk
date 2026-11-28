"""Fast sheet previews (storage plan): a light picture of every sheet is made when the engine draws, the viewer opens on
it, and the sharp picture is fetched only when zoomed in past 150 %. Older drawings get their light pictures on first
view. Needs ENGINES_DIR."""
import os, sys, tempfile, glob
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def test_previews():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    with db.Session() as s:
        d = s.query(db.OrderLine).filter_by(no=1).one().drawing
        pdf, did = d.files["pdf"], d.id
    light = sorted(glob.glob(pdf[:-4] + ".sheet*.png"))
    assert light, "light pictures are made when the engine draws"
    v = c.get(A + "/view/1").text
    assert 'src="/drawings/%d/sheet?page=0' % did in v and 'data-big="/drawings/%d/big?page=0' % did in v
    r = c.get("/drawings/%d/sheet?page=0" % did)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert len(r.content) < len(c.get("/drawings/%d/big?page=0" % did).content)                 # lighter than the sharp one
    assert c.get("/drawings/%d/sheet?page=99" % did).status_code == 404                           # no such sheet
    # issued revisions: the same, from the issued PDF
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    with db.Session() as s:
        r = s.query(db.Revision).first()
        rid, rpdf = r.id, r.files["pdf"]
    assert c.get("/revisions/%d/sheet?page=0" % rid).status_code == 200
    assert 'src="/revisions/%d/sheet?page=0' % rid in c.get(A + "/view/1").text
    # old data: a drawing made before light pictures existed gets them on first view
    for p in glob.glob(rpdf[:-4] + ".sheet*.png"):
        os.remove(p)
    assert c.get("/revisions/%d/sheet?page=0" % rid).status_code == 200 and glob.glob(rpdf[:-4] + ".sheet*.png")
    # a drawing that could not be made: no picture, no crash
    with db.Session() as s:
        d = s.query(db.OrderLine).filter_by(no=2).one().drawing
        d.status, d.files = "failed", {}; s.commit(); did2 = d.id
    assert c.get("/drawings/%d/sheet?page=0" % did2).status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
