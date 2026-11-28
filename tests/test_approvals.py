"""Approvals (screen 7): who decides what (decision 8), option A / B / reject for engine requests, permission for a change
after client approval, and the task every decision creates for the person who asked. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, auth

A = "/orders/Q-2026-10-444"
KUSHAL, SUSHIL, ANCHAL = 1, 2, 3


def test_who_decides():
    k, s, a = (NS(role="admin", email="kushal@encoreprimefurniture.com"), NS(role="drawing", email="sushil@encoreprimefurniture.com"),
               NS(role="coordinator", email="info@encoreprimefurniture.com"))
    assert (auth.level(k), auth.level(s), auth.level(a)) == ("admin", "admin2", "staff")
    assert all(auth.can_decide(k, x) for x in ("engine", "after_approval", "revision"))
    assert auth.can_decide(s, "revision") and not auth.can_decide(s, "engine") and not auth.can_decide(s, "after_approval")
    assert not any(auth.can_decide(a, x) for x in ("engine", "after_approval", "revision"))


c = TestClient(app)


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def _tasks(who):
    with db.Session() as s:
        return [(t.text, t.status) for t in s.query(db.Task).filter_by(assignee=who).order_by(db.Task.id)]


def test_engine_requests():
    as_(ANCHAL)
    c.get(A)
    assert c.get("/approvals").status_code == 403 and "/approvals" not in c.get("/").text.split("<main")[0]
    for text in ("Glass shutter on the side unit", "Raceway 1500 long", "Wire box in the centre"):
        c.post("/engine-requests", data={"text": text, "order_ref": "Q-2026-10-444", "product": "Product 2: Storage ESP 01"})
    as_(SUSHIL)                                                  # secondary administrator: sees, cannot decide engine changes
    v = c.get("/approvals").text
    assert "Approvals" in v and "Only Kushal decides this" in v and "Secondary administrator" in v
    assert c.post("/approvals/engine/1/a").status_code == 403
    as_(KUSHAL)
    v = c.get("/approvals").text
    assert "Option A · this client only" in v and "off by default" in v and "Approve option B &amp; send to engine work" in v
    assert "commit" not in v.lower()                              # the desk never changes engines
    c.post("/approvals/engine/1/a", data={"note": "Only for Achievers"})
    c.post("/approvals/engine/2/b")
    c.post("/approvals/engine/3/reject", data={"note": "Not possible"})
    assert c.post("/approvals/engine/3/a").status_code == 400     # decided already
    with db.Session() as s:
        st = {r.id: (r.status, r.option) for r in s.query(db.EngineRequest)}
    assert st == {1: ("approved", "A"), 2: ("engine_work", "B"), 3: ("declined", "")}
    t = _tasks("Anchal Dawar")
    assert t[0][0].startswith("Approved, this client only: apply it in this product's file") and t[0][0].endswith("Note: Only for Achievers")
    assert t[1][0].startswith("Sent to engine work: it is built as a generic option, off by default") and t[2][0].startswith("Rejected:")
    v = c.get("/approvals?tab=engine&show=2").text
    assert "push an engine- branch" in v
    assert "Engine request decided by Kushal Singhal" in c.get(A + "/work").text
    assert "Sent to engine work" in c.get("/engine-requests").text                   # the older list knows the new state
    c.post("/engine-requests/2/built")
    with db.Session() as s:
        assert s.get(db.EngineRequest, 2).status == "built"

    # the person who asked sees the task on the project and marks it done
    as_(ANCHAL)
    h = c.get(A).text
    assert "Task for Anchal Dawar (Product 2): Approved, this client only" in h and 'action="/tasks/1/done"' in h
    c.post("/tasks/1/done", headers={"referer": "http://testserver" + A})
    assert _tasks("Anchal Dawar")[0][1] == "done" and c.post("/tasks/2/done").status_code in (200, 303)


def test_change_after_client_approval():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(SUSHIL)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "6"})
    with db.Session() as s:
        rid = s.query(db.Revision).filter_by(number="Encore-Nov-2026-06-02").one().id
    c.post("%s/answer-from-client/%d" % (A, rid), data={"outcome": "approved", "name": "Mr Singh", "how": "email", "on": "2026-11-27"},
           files={"proof": ("reply.eml", b"Approved", "message/rfc822")})
    v = c.get(A + "/view/2").text
    assert "so it is locked" in v and "Ask Kushal for permission" in v and 'action="/orders/Q-2026-10-444/view/2/ai"' not in v
    assert c.post(A + "/view/2/permission", data={"text": "   "}).status_code == 400   # empty: say what has to change
    c.post(A + "/view/2/permission", data={"text": "Client wants 2 shelves"})
    assert c.post("/approvals/after_approval/1/approve").status_code == 403           # Sushil cannot unlock (decision 8)
    v = c.get("/approvals?tab=after").text
    assert "Client wants 2 shelves" in v and "Waiting for you" in v
    as_(KUSHAL)
    c.post("/approvals/after_approval/1/approve")
    assert c.post("/approvals/after_approval/1/reject").status_code == 400            # decided once only
    v = c.get(A + "/view/2").text
    assert 'action="/orders/Q-2026-10-444/view/2/ai"' in v and "so it is locked" not in v                 # unlocked for this change
    assert _tasks("Sushil Singhal")[0][0].startswith("Allowed: change Product 2 in the change panel")
    # once issued again, the new revision needs the client again; the old permission does not carry over
    c.post(A + "/change", data={"text": "Product 2: 2 shelves", "line": "2"})
    with db.Session() as s:
        cid = s.query(db.Change).filter_by(status="asked").one().id
    c.post("%s/change/%d/yes" % (A, cid))
    c.post(A + "/issue")
    with db.Session() as s:
        rid2 = s.query(db.Revision).filter_by(number="Encore-Nov-2026-06-02", rev="B").one().id
    c.post("%s/answer-from-client/%d" % (A, rid2), data={"outcome": "approved", "name": "Mr Singh", "how": "email", "on": "2026-11-28"},
           files={"proof": ("reply.eml", b"Approved", "message/rfc822")})
    assert "so it is locked" in c.get(A + "/view/2").text

    # revision raises: a secondary administrator may decide them
    with db.Session() as s:
        s.add(db.ChangeRequest(kind="revision", order_ref="Q-2026-10-444", line_no=1, text="Quantity 3 to 4 after issue", asked_by="Anchal Dawar")); s.commit()
        cr = s.query(db.ChangeRequest).filter_by(kind="revision").one().id
    as_(SUSHIL)
    c.post("/approvals/revision/%d/approve" % cr)
    with db.Session() as s:
        assert s.get(db.ChangeRequest, cr).status == "approved"
    assert _tasks("Anchal Dawar")[-1][0].startswith("Revision allowed for Product 1")



def test_allowed_change_opens_the_panel_in_production():
    """Staging, Q-2026-11-506 (28-Nov 10:04): the order was approved and released to production; after Kushal allowed a change
    the panel stayed locked, and a change-panel proposal made before Rev A was issued kept the product "waiting" with no
    Accept / Undo to clear it. Allowing must open the panel at any stage; a proposal older than the issue is dropped."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    import datetime
    as_(SUSHIL)
    with db.Session() as s:                                    # the client approves the other drawings: the order is approved
        line = {l.no: l.id for l in s.query(db.OrderLine)}
        r1 = s.query(db.Revision).filter_by(line_id=line[1]).order_by(db.Revision.id.desc()).first()
        others = [r.id for no in (1, 3) for r in [s.query(db.Revision).filter_by(line_id=line[no]).order_by(db.Revision.id.desc()).first()] if r]
        # the old proposal: made on Product 1 before its Rev A was issued (the 1500 test), never accepted or undone
        s.add(db.AiTurn(line_id=line[1], shown="d1", typed="make it 1500 long", kind="change", said="Length 1200 -> 1500", status="trial",
                        values={"model.L": 1500}, at=r1.issued_at - datetime.timedelta(hours=1))); s.commit()
    for rid in others:
        c.post("%s/answer-from-client/%d" % (A, rid), data={"outcome": "approved", "name": "Mr Singh", "how": "email", "on": "2026-11-28"},
               files={"proof": ("reply.eml", b"Approved", "message/rfc822")})
    as_(KUSHAL)
    c.post(A + "/step/released", data={"note": ""})
    from desk.app import load
    assert load("Q-2026-10-444").stage == "production"
    v = c.get(A + "/view/1").text
    assert "so it is locked" in v and "Accept or Undo the change first" not in v              # the old proposal no longer blocks
    with db.Session() as s:
        assert s.query(db.AiTurn).filter_by(line_id=line[1], status="trial").count() == 0
    as_(SUSHIL)
    c.post(A + "/view/1/permission", data={"text": "Client wants it 1500 long"})
    with db.Session() as s:
        cr = s.query(db.ChangeRequest).filter_by(kind="after_approval", line_no=1).one().id
    as_(KUSHAL)
    c.post("/approvals/after_approval/%d/approve" % cr)
    as_(SUSHIL)
    v = c.get(A + "/view/1").text
    assert 'action="/orders/Q-2026-10-444/view/1/ai"' in v and "so it is locked" not in v      # open, although in production
    assert "Accept or Undo the change first" not in v and "Dropped: Rev A was issued after this proposal" in v


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
