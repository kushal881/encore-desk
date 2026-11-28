"""Issue #34: client approvals column. One card per issued drawing with the client's answer; the portal's change requests
leave the main conversation; Apply change -> the AI change panel; Re-issue to client (next revision, combined PDF, revised
email to the access list); Reply / Decline shown to the client on the portal. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
import desk.app as appmod
from desk.app import app
from desk import db, engines, issuing, clients as cl, portal

c = TestClient(app)
A = "/orders/Q-2026-10-444"
CLIENT = "singh@achievers.in"


def rev(no):
    with db.Session() as s:
        return s.query(db.Revision).join(db.OrderLine).filter(db.OrderLine.no == no).order_by(db.Revision.id.desc()).first()


def card(no):
    return next(x for x in cl.cards(appmod.load("Q-2026-10-444")) if x["l"].no == no)


def test_client_column(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    sent = []
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: sent.append((to, subject, body, name)))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)              # the desk's own change rules stand in for the AI
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    c.post(A + "/access", data={"email": CLIENT, "name": "Mr Singh", "can": "approve"})
    assert [card(n)["status"] for n in (1, 2, 3)] == ["waiting"] * 3 and "Client approvals" in c.get(A + "/work").text
    # product 1 approved: the card says so and offers nothing
    c.post("%s/answer-from-client/%d" % (A, rev(1).id), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "ok on call"})
    assert card(1)["status"] == "approved" and not card(1)["open"]
    assert c.post("%s/client/%d/apply" % (A, card(1)["ap"].id), data={"text": "x"}).status_code == 400
    # a change asked on the portal, with a numbered mark (as the portal records it)
    r2 = rev(2)
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        l2 = s.query(db.OrderLine).filter_by(no=2).one()
        s.add(db.Mark(line_id=l2.id, shown="r%d" % r2.id, no=1, x=40, y=50, label="Increase size to 1050mm", by="Client: Mr Singh"))
        s.add(db.Approval(order_id=o.id, revision_id=r2.id, outcome="changes", name="Mr Singh", email=CLIENT, how="portal", note="[1] Increase size to 1050mm", by="Portal: " + CLIENT))
        s.add(db.Message(order_id=o.id, who="desk", text="Changes asked on the portal by Mr Singh (%s) on %s Rev A: [1] Increase size to 1050mm" % (CLIENT, r2.number)))
        s.commit()
    h = c.get(A + "/work").text
    assert "Changes asked" in h and "[1]</span> Increase size to 1050mm" in h and "View the marked-up drawing" in h
    assert "Changes asked on the portal by" not in h                    # not in the main conversation any more
    assert "Client asked for changes (1)" in c.get("/").text and "The client asked for changes on 1 drawing" in c.get(A).text
    aid = card(2)["ap"].id
    # empty note: refused; then apply -> a pending change in the change panel; re-issue refused until Accept / Undo
    assert c.post("%s/client/%d/apply" % (A, aid), data={"text": "  "}).status_code == 400
    c.post("%s/client/%d/apply" % (A, aid), data={"text": "Product 2: make it 1050 long"})
    with db.Session() as s:
        tr = s.query(db.AiTurn).filter_by(status="trial").one()
    assert card(2)["ai_waiting"] and "A change is waiting in the change panel" in c.get(A + "/work").text
    r = c.post("%s/client/%d/reissue" % (A, aid))
    assert r.status_code == 400 and "Accept or Undo" in r.text
    c.post("%s/view/2/ai/%d/accept" % (A, tr.id))
    assert card(2)["can_reissue"] and "Re-issue to client" in c.get(A + "/work").text
    h = c.get("%s/client/%d/reissue" % (A, aid)).text
    assert "Rev B" in h and CLIENT in h and "Revised drawings: Q-2026-10-444" in h
    c.post("%s/client/%d/reissue" % (A, aid))
    assert rev(2).rev == "B" and rev(2).spec["model"]["L"] == 1050                       # Rev B carries the client's change
    assert sent and sent[-1][0] == [CLIENT] and sent[-1][1].startswith("Revised drawings") and "/portal/" in sent[-1][2] and sent[-1][3]   # combined PDF attached
    assert card(2)["status"] == "waiting" and "Re-issued after the client" in c.get(A + "/work").text
    with db.Session() as s:
        assert s.query(db.ClientAction).filter_by(kind="reissued").one().text.endswith("sent to " + CLIENT)
    # changes asked by phone (recorded by staff): the same card; Reply, then Decline closes it; the portal shows both
    c.post("%s/answer-from-client/%d" % (A, rev(3).id), data={"outcome": "changes", "name": "Mr Singh", "how": "phone", "note": "Hinge on the left please"})
    a3 = card(3)["ap"].id
    assert card(3)["open"] and "Hinge on the left please" in c.get(A + "/work").text
    assert c.post("%s/client/%d/reply" % (A, a3), data={"text": "", "kind": "reply"}).status_code == 400
    c.post("%s/client/%d/reply" % (A, a3), data={"text": "We are checking with production.", "kind": "reply"})
    assert card(3)["open"]
    c.post("%s/client/%d/reply" % (A, a3), data={"text": "The hinge side is fixed by the wall; it stays on the right.", "kind": "reject"})
    assert not card(3)["open"] and appmod.load("Q-2026-10-444").client_open == 0
    d3 = next(d for d in portal.drawings(appmod.load("Q-2026-10-444")) if d["l"].no == 3)
    assert [x.kind for x in d3["replies"]] == ["reply", "reject"]
    # old data: a change request recorded before this column existed (no actions, older answer kept) still shows
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Approval(order_id=o.id, revision_id=rev(1).id, outcome="changes", name="Mr Singh", how="", note="Old request: 2 shelves")); s.commit()
    assert card(1)["status"] == "changes" and card(1)["open"] and "Old request: 2 shelves" in c.get(A + "/work").text
    # someone else's answer id on this order: not found
    assert c.post("%s/client/999/apply" % A, data={"text": "x"}).status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
