"""Screen 7a: My requests. A person's own requests with the admin decision and the task; Apply & regenerate for an
approved engine change (option B) or revision raise; the change panel for option A and changes after approval; Mark
task done. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, myrequests as mr

c = TestClient(app)
A = "/orders/Q-2026-10-444"
KUSHAL, SUSHIL, ANCHAL = 1, 2, 3


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def mine(name="Anchal Dawar"):
    with db.Session() as s:
        return {(r["kind"], r["id"]): r for r in mr.requests_of(s, name)}


def test_my_requests():
    assert c.get("/my-requests", follow_redirects=False).headers["location"] == "/login"
    as_(ANCHAL)
    c.get(A)
    h = c.get("/my-requests").text
    assert "You have not asked for anything yet" in h and 'href="/my-requests"' in h          # staff: menu item
    as_(KUSHAL)
    assert 'href="/my-requests"' not in c.get("/").text                                       # the administrator decides instead
    with db.Session() as s:
        for txt, prod in (("Glass shutter", "Product 1: Workstation"), ("Raceway 1500 long", "Product 1: Workstation"), ("Remove grommet", "Product 2: Storage")):
            s.add(db.EngineRequest(order_ref="Q-2026-10-444", product=prod, text=txt, asked_by="Anchal Dawar"))
        s.add(db.ChangeRequest(kind="revision", order_ref="Q-2026-10-444", line_no=2, product="Product 2: Storage", text="Raise to L1500", asked_by="Anchal Dawar"))
        s.add(db.ChangeRequest(kind="after_approval", order_ref="Q-2026-10-444", line_no=3, product="Product 3: Pedestal", text="Hinge left", asked_by="Anchal Dawar"))
        s.add(db.EngineRequest(order_ref="Q-2026-10-444", product="Product 3", text="Someone else's", asked_by="Sushil Singhal"))
        s.add(db.EngineRequest(order_ref="", product="", text="Old request without a project", asked_by="Anchal Dawar"))   # old data
        s.commit()
    c.post("/approvals/engine/1/a", data={"note": "Use 5 mm toughened, frosted"})
    c.post("/approvals/engine/2/b")
    c.post("/approvals/engine/3/reject", data={"note": "client approved RevA with grommet"})
    c.post("/approvals/after_approval/2/approve")
    as_(ANCHAL)
    m = mine()
    assert [m[("engine", i)]["status"] for i in (1, 2, 3, 5)] == ["Approved", "Approved", "Rejected", "Pending"]
    assert m[("revision", 1)]["status"] == "Pending" and m[("after_approval", 2)]["status"] == "Approved"
    assert ("engine", 4) not in m                                                              # only my own
    assert m[("engine", 1)]["action"] == ("Open the change panel", "panel") and m[("engine", 2)]["action"] == ("Apply & regenerate", "apply")
    assert m[("engine", 3)]["action"] == ("Mark task done", "done") and m[("after_approval", 2)]["action"][1] == "panel"
    h = c.get("/my-requests").text
    assert "Use 5 mm toughened, frosted" in h and "Old request without a project" in h and "No project" in h
    assert "Rejected requests (1)" in c.get("/my-requests?show=Rejected").text and "Pending requests (2)" in c.get("/my-requests?show=Pending").text
    assert "All my requests" in c.get("/my-requests?show=bogus").text
    # cannot apply: pending, someone else's, or a change that is made in the change panel
    assert c.post("/my-requests/revision/1/apply").status_code == 400
    assert c.post("/my-requests/engine/4/apply").status_code == 404
    assert c.post("/my-requests/engine/1/apply").status_code == 400
    # rejected: mark the task done
    tid = m[("engine", 3)]["task"].id
    c.post("/tasks/%d/done" % tid)
    assert mine()[("engine", 3)]["status"] == "Done"
    # an approved revision raise on a product with questions still open: refused
    as_(SUSHIL); c.post("/approvals/revision/1/approve"); as_(ANCHAL)
    r = c.post("/my-requests/revision/1/apply")
    assert r.status_code == 400 and "questions to answer first" in r.text


def test_apply_and_regenerate():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(ANCHAL)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    with db.Session() as s:
        l1 = s.query(db.OrderLine).filter_by(no=1).one()
        before = l1.drawing.made_at
        s.add(db.AiTurn(line_id=l1.id, typed="1500 long", kind="change", status="trial")); s.commit()   # an AI change waiting
        tid = s.query(db.AiTurn).filter_by(status="trial").one().id
    r = c.post("/my-requests/engine/2/apply")
    assert r.status_code == 400 and "Accept or Undo" in r.text
    c.post("%s/view/1/ai/%d/undo" % (A, tid))
    r = c.post("/my-requests/engine/2/apply", follow_redirects=False)
    assert r.headers["location"] == A + "/view/1"
    with db.Session() as s:
        l1 = s.query(db.OrderLine).filter_by(no=1).one()
        assert l1.drawing.status == "preview" and l1.drawing.made_at.replace(tzinfo=None) > before.replace(tzinfo=None)
    assert mine()[("engine", 2)]["status"] == "Done"
    with db.Session() as s:
        assert any(m.text.startswith("Engine change applied by Anchal Dawar: Product 1 redrawn") for m in s.query(db.Message))
    assert c.post("/my-requests/engine/2/apply").status_code == 400                         # done: nothing more to apply
    # the revision raise, now that the questions are answered
    c.post("/my-requests/revision/1/apply")
    assert mine()[("revision", 1)]["status"] == "Done"
    # an approved request on a product that was removed since: a plain refusal
    with db.Session() as s:
        s.add(db.ChangeRequest(kind="revision", order_ref="Q-2026-10-444", line_no=9, product="Product 9", text="x", asked_by="Anchal Dawar")); s.commit()
        rid = s.query(db.ChangeRequest).filter_by(line_no=9).one().id
    as_(SUSHIL); c.post("/approvals/revision/%d/approve" % rid); as_(ANCHAL)
    r = c.post("/my-requests/revision/%d/apply" % rid)
    assert r.status_code == 400 and "no longer in" in r.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
