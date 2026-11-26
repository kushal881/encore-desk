"""Dashboard, viewer, steps after issue, engine requests, photographs and remakes. Needs ENGINES_DIR."""
import os, sys, io, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db

c = TestClient(app)
A, B = "/orders/Q-2026-10-444", "/orders/SAMPLE-REPEAT-1"


def test_more():
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    c.post("/login/dev/3")                                                   # Anchal Dawar, coordinator
    h = c.get("/").text
    assert "No active projects" in h and "Q-2026-10-444" in h and "SAMPLE-REPEAT-1" in h      # offered as samples
    c.get(A + "/work")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    h = c.post(A + "/draw").text
    assert "Product 1" in h and "Line 1" not in h and "View full size" in h and "Next step" not in h
    v = c.get(A + "/view/2")
    assert v.status_code == 200 and "Preview, not issued" in v.text and "/drawings/2/big" in v.text
    assert c.get("/drawings/2/big").headers["content-type"] == "image/png"
    r = c.get("/drawings/1/pdf"); assert r.headers["content-disposition"].startswith("inline")
    r = c.get("/drawings/1/pdf?download=1"); assert r.headers["content-disposition"].startswith("attachment")
    h = c.post(A + "/change", data={"text": "make it 1400 long", "line": "1"}).text           # a change aimed at one drawing
    assert "Product 1, understood as:" in h and "length 1400 mm" in h
    with db.Session() as s:
        cid = s.query(db.Change).filter_by(status="asked").one().id
    c.post("%s/change/%d/no" % (A, cid))
    # steps after issue
    assert c.post(A + "/step/sent").status_code == 400                                       # nothing issued yet
    h = c.post(A + "/issue", data={"folder_no": "6"}).text
    assert "Next step" in h and "Mark as sent to client" in h
    assert "Waiting for the client" in c.post(A + "/step/sent", data={"note": "Email to Mr Singh"}).text
    assert c.post(A + "/step/approved", data={"note": ""}).status_code in (400, 422)
    assert c.post(A + "/step/released", data={"note": "Client wants it this week"}).status_code == 403    # a coordinator cannot release before approval
    h = c.post(A + "/step/approved", data={"note": "Mr Singh, by email"}).text
    assert "Release to production" in h and "Client approval recorded" in h
    h = c.post(A + "/step/released").text
    assert "Mark project completed" in h
    d = c.get("/").text
    assert "In production" in d and "Encore-Nov-2026-06-02 Rev A" in d and "3 of 3 issued" in d
    # photograph, kept by product name
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (40, 30), (120, 90, 60)).save(buf, "PNG")
    assert c.post(A + "/photo/2", files={"file": ("p.png", buf.getvalue(), "image/png")}).status_code == 200
    assert c.post(A + "/photo/2", files={"file": ("p.txt", b"not a picture", "text/plain")}).status_code == 400
    # a later order of the same client: remake one product, ask for an engine addition for the other
    h = c.get(B + "/work").text
    assert "/photos/1" in h and "Same as a past drawing" in h and "Encore-Nov-2026-06-02 Rev A" in h    # same product name -> same photograph
    with db.Session() as s:
        rid = s.query(db.Revision).filter_by(number="Encore-Nov-2026-06-02").one().id
        q2 = [q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "SAMPLE-REPEAT-1")]
    h = c.post(B + "/remake/1", data={"rid": rid}).text
    assert "Remake of a past drawing" in h and "Preview, not issued" in h
    with db.Session() as s:
        fam = s.query(db.Question).filter_by(key="family").one().id
    c.post("%s/answer/%d" % (B, fam), data={"value": "None of these"})
    h = c.post(B + "/draw").text
    assert "Could not draw" in h and "Ask for an engine addition" in h and "Issue the ready drawings" in h
    h = c.post("/engine-requests", data={"text": "Add Conference Table CT 01 to the engine", "order_ref": "SAMPLE-REPEAT-1", "product": "Conference Table CT 01"}).text
    assert "Engine addition requested by Anchal Dawar" in h
    assert c.post("/engine-requests/1/approve").status_code == 403                          # only the administrator decides
    h = c.post(B + "/issue").text
    with db.Session() as s:
        r = s.query(db.Revision).order_by(db.Revision.id.desc()).first()
        old = s.get(db.Revision, rid)
        assert (r.number, r.rev, r.reason) == ("Encore-Nov-2026-06-04", "A", "Remake of Encore-Nov-2026-06-02 Rev A"), (r.number, r.rev, r.reason)
        assert r.spec["model"] == old.spec["model"] and r.spec["item"]["qty"] == 4 and r.spec["client"]["drawing_id"] == "SAMPLE-REPEAT-1"
        assert os.path.exists(r.files["pdf"])
    c.post(A + "/step/completed")
    d = c.get("/").text
    assert "SAMPLE-REPEAT-1" in d and ">Q-2026-10-444<" not in d.split("Open an order")[0] and "Completed projects (1)" in d
    assert "Q-2026-10-444" in c.get("/?show=completed").text
    c.post("/logout"); c.post("/login/dev/1")                                               # Kushal Singhal, administrator
    h = c.get("/engine-requests").text
    assert "Waiting for decision" in h and "Approve" in h
    h = c.post("/engine-requests/1/approve", data={"note": "Needed for Achiever"}).text
    assert "Approved, to be built" in h and "Engine: Add Conference Table CT 01 to the engine" in h
    assert "Built and in the engine" in c.post("/engine-requests/1/built").text
