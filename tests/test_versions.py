"""Issued drawings stay on their engine version; production and subcontractor sets. Needs two approved versions on disk
(V2_DIR and V3_DIR point at checkouts of approved v2 and v3)."""
import os, sys, tempfile, pytest
V2, V3 = os.environ.get("V2_DIR", "/home/claude/approved_engines"), os.environ.get("V3_DIR", "/home/claude/work/v3")
if not (os.path.isdir(V2) and os.path.isdir(V3)):
    pytest.skip("two engine versions are needed", allow_module_level=True)
home = tempfile.mkdtemp()
os.symlink(V2, os.path.join(home, "v2")); os.symlink(V3, os.path.join(home, "v3"))
os.environ.pop("ENGINES_DIR", None)
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3"), FILES_DIR=tempfile.mkdtemp(), DESK_DEV_LOGIN="1", ENGINES_HOME=home, ENGINES_KEY="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

c = TestClient(app)
U = "/orders/Q-2026-10-444"


def use(v):
    engines.ENGINES_DIR, engines.VERSION = os.path.join(home, v), v


def test_versions():
    use("v2")
    c.post("/login/dev/1")
    c.get(U + "/work")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (U, qs[key]), data={"value": val})
    c.post(U + "/draw")
    h = c.post(U + "/issue", data={"folder_no": "6"}).text
    assert h.count("Issued Rev A") == 3 and "Redraw on" not in h
    assert c.post(U + "/sets/production").status_code == 400                         # not released yet
    use("v3")                                                                        # a new approved version arrives
    h = c.post(U + "/draw").text                                                     # redrawing does not move issued drawings
    assert h.count("Issued Rev A") == 3 and h.count("kept on it. Latest is v3") == 3
    with db.Session() as s:
        assert {d.engine_version for d in s.query(db.Drawing)} == {"v2"}
    h = c.post(U + "/engine/1").text                                                 # move one product on purpose
    assert h.count("Issued Rev A") == 2 and h.count("Preview, not issued") == 1 and "redrawn on engines v3 (was v2)" in h
    assert c.post(U + "/engine/1").status_code == 400
    c.post(U + "/issue")
    with db.Session() as s:
        revs = [(r.number[-2:], r.rev, r.engine_version) for r in s.query(db.Revision).order_by(db.Revision.id)]
        assert revs == [("01", "A", "v2"), ("02", "A", "v2"), ("03", "A", "v2"), ("01", "B", "v3")], revs
        last = s.query(db.Revision).order_by(db.Revision.id.desc()).first()
        assert last.reason == "redrawn on engines v3 (was v2)" and last.spec["revisions"][-1]["desc"] == "engines v3"
    h = c.post(U + "/draw").text
    assert "Issued Rev B" in h and h.count("Issued Rev A") == 2 and h.count("Latest is v3") == 2
    # production and subcontractor drawings
    c.post(U + "/step/released", data={"note": "Client wants it this week"})
    h = c.post(U + "/sets/production", data={"job_order": "JO 26-27-10-0108", "job_order_date": "26 NOV 2026", "deadline": "10 DEC 2026"}).text
    assert "Production drawings made for Encore-Nov-2026-06-01 Rev B" in h and "Metal Shop" in h and "Complete product" in h
    with db.Session() as s:
        sets = {ps.line_id: ps for ps in s.query(db.ProductionSet)}
        assert len(sets) == 3 and {ps.engine_version for ps in sets.values()} == {"v2", "v3"}
        one = next(ps for ps in sets.values() if ps.engine_version == "v3")
        assert [f["title"] for f in one.files][0] == "Complete product" and len(one.files) >= 3 and all(os.path.exists(f["pdf"]) for f in one.files)
        sid = one.id
        assert s.query(db.Order).one().job_order == "JO 26-27-10-0108"
    v = c.get("/sets/%d/1" % sid)
    assert v.status_code == 200 and "Production drawings" in v.text
    assert c.get("/sets/%d/1/page/0" % sid).headers["content-type"] == "image/png"
    assert c.get("/sets/%d/9" % sid).status_code == 404
    with db.Session() as s:
        assert one.details == dict(job_order="JO 26-27-10-0108", job_order_date="26 NOV 2026", deadline="10 DEC 2026")
    global KEEP
    KEEP = [f["pdf"] for f in one.files]
    h = c.post(U + "/sets/vendor", data={"deadline": "05 DEC 2026"}).text
    assert "Subcontractor drawings made for" in h
    # a later revision marks the production set as out of date
    c.post(U + "/change", data={"text": "make it 1500 long", "line": "2"})
    with db.Session() as s:
        cid = s.query(db.Change).filter_by(status="asked").one().id
    c.post("%s/change/%d/yes" % (U, cid)); c.post(U + "/issue")
    h = c.get(U + "/work").text
    assert h.count("Made from an older revision") == 2                               # production and subcontractor sets of product 2
    with db.Session() as s:
        assert s.query(db.Revision).order_by(db.Revision.id.desc()).first().engine_version == "v2"      # still on its own version


def test_keep_for_looking():
    """Copies the production sheets to LOOK_DIR when it is set, to look at them."""
    out = os.environ.get("LOOK_DIR")
    if out:
        import shutil
        for i, p in enumerate(KEEP):
            shutil.copy(p, os.path.join(out, "prod_%d.pdf" % i))
