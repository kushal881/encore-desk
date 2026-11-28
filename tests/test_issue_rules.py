"""Issue rules found on staging (Q-2026-11-506, 28-Nov-2026):
- a product with an AI change still waiting (Accept / Undo not pressed) cannot be issued: "Accept or Undo the change first";
- after issue the viewer shows the issued revision, also when the preview was made on an older engine version (old data),
  and nothing is listed again as "new revision after Rev A";
- after the client's approval "mark ready to issue" is gone and the drawing is not offered for issue again;
- an approval by email or signed PDF needs the proof file; by phone, a note (empty values refused).
Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, assistant

A = "/orders/Q-2026-10-444"
c = TestClient(app)
PROOF = {"proof": ("approval.eml", b"From: singh@achievers.in\n\nApproved.", "message/rfc822")}


def rev(no):
    with db.Session() as s:
        return s.query(db.Revision).join(db.OrderLine).filter(db.OrderLine.no == no).order_by(db.Revision.id.desc()).first()


def test_issue_rules(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    with db.Session() as s:                                    # old data: product 1's preview was made on an older engine version
        d1 = s.query(db.OrderLine).filter_by(no=1).one().drawing
        d1.engine_version = "v6"; s.commit()

    # an AI change waiting on product 2 (the staging case): it cannot be issued
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(assistant, "_call", lambda system, user: dict(kind="change", said="Length 1200 -> 1500", answer="",
                                                                       changes=[dict(path="model.L", value="1500")]))
    c.post(A + "/view/2/ai", data={"text": "make it 1500 long", "marker": "0"})
    with db.Session() as s:
        tid = s.query(db.AiTurn).one().id
        assert s.query(db.AiTurn).one().status == "trial"
    v = c.get(A + "/issue").text
    assert "Product 2: Accept or Undo the change first" in v and "Ready to issue (2)" in v
    assert "Accept or Undo the change first" in c.get(A + "/view/2").text and "mark ready to issue</button>" not in c.get(A + "/view/2").text
    assert c.post(A + "/view/2/ready", data={"shown": "d1"}).status_code == 400
    r = c.post(A + "/issue", data={"folder_no": "6", "nos": "2", "back": "issue"})
    assert r.status_code == 400 and "Product 2: Accept or Undo the change first." in r.text
    c.post(A + "/issue", data={"folder_no": "6", "back": "issue"})                   # the others go ahead
    assert rev(2) is None and rev(1) is not None and rev(3) is not None

    # after issue: the viewer shows Rev A (even for the preview made on the older engines), nothing listed again
    v = c.get(A + "/view/1").text
    assert "Rev A" in v and "Preview, not issued" not in v and "mark ready to issue</button>" not in v
    v = c.get(A + "/issue").text
    assert "new revision after Rev A" not in v

    # Undo, then issue product 2: Rev A is the size that was there, not the change that was never accepted
    c.post("%s/view/2/ai/%d/undo" % (A, tid))
    c.post(A + "/issue", data={"nos": "2", "back": "issue"})
    assert rev(2).spec["model"]["L"] == 1200 and rev(2).rev == "A"

    # approval proof: email / signed PDF need the file, phone needs a note
    r1 = rev(1).id
    for how in ("email", "signed PDF"):
        v = c.post("%s/answer-from-client/%d" % (A, r1), data={"outcome": "approved", "name": "Mr Singh", "how": how}).text
        assert "Attach the client" in v
    assert "write a note" in c.post("%s/answer-from-client/%d" % (A, r1), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": " "}).text
    assert "Enter who answered" in c.post("%s/answer-from-client/%d" % (A, r1), data={"outcome": "approved", "name": "", "how": "phone"}).text
    with db.Session() as s:
        assert s.query(db.Approval).count() == 0
    c.post("%s/answer-from-client/%d" % (A, r1), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "Called 11:30, approved as drawn", "on": "2026-11-28"})
    c.post("%s/answer-from-client/%d" % (A, rev(3).id), data={"outcome": "approved", "name": "Mr Singh", "how": "email", "on": "2026-11-28"}, files=PROOF)
    with db.Session() as s:
        assert s.query(db.Approval).count() == 2

    # after approval: no "mark ready to issue", and the drawing is not offered for issue again
    v = c.get(A + "/view/1").text
    assert "Approved by the client" in v and "mark ready to issue</button>" not in v and "so it is locked" in v
    with db.Session() as s:                                    # even with a newer preview drawn after approval
        s.query(db.OrderLine).filter_by(no=1).one().drawing.engine_version = "v12"; s.commit()
    assert "mark ready to issue</button>" not in c.get(A + "/view/1").text
    assert "Product 1: approved by the client, locked" in c.get(A + "/issue").text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
