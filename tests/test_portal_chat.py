"""Client portal drawing page, chat style (issue #83): the drawing on the left, "Talk to Encore about this drawing" on the
right; the client's marked comments go out as ONE change request to the designer (never the AI); Encore's replies and a new
revision show in the same chat; the Approve box with the email code sits right above the message box.
Covers old change requests (history), a change still with the designer, an approved / locked drawing, an empty send, and a
client who may only see some orders. Needs ENGINES_DIR."""
import os, sys, tempfile, re, json
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, issuing, assistant

A, P = "/orders/Q-2026-10-444", "/portal/orders/Q-2026-10-444"
SINGH = "singh@achievers.in"
staff, c = TestClient(app), TestClient(app)
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))
    monkeypatch.setattr(assistant, "ask", lambda *a, **k: pytest.fail("the client's request must never go to the AI"))


def code_for(email, purpose="Your Encore code"):
    m = next(m for m in reversed(MAIL) if m["to"] == [email] and m["subject"].startswith(purpose))
    return re.search(r"(\d{6})", m["subject"]).group(1)


def revs():
    with db.Session() as s:
        return {r.number: (r.id, r.rev) for r in s.query(db.Revision).order_by(db.Revision.id)}


def test_chat_flow():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    staff.post("/login/dev/2"); staff.get(A); staff.get("/orders/SAMPLE-REPEAT-1")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for k, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        staff.post("%s/answer/%d" % (A, qs[k]), data={"value": val})
    staff.post(A + "/draw")
    staff.post(A + "/issue", data={"folder_no": "6", "nos": "1,3", "back": "issue"})
    staff.post(A + "/access", data={"email": SINGH, "name": "Mr Singh", "can": "approve"})
    rid1, rid2 = [v[0] for v in revs().values()]
    c.post("/portal/code", data={"email": SINGH})
    c.post("/portal/signin", data={"email": SINGH, "code": code_for(SINGH), "keep": "1"})

    # the page: drawing + chat, Rev A message from the designer, the Approve box above the message box
    v = c.get("%s/d/%d" % (P, rid1)).text
    assert "Talk to Encore about this drawing" in v and "Rev A of" in v and "is ready for you" in v
    assert v.index('id="approve"') < v.index('id="pform"') and "Approve Rev A" in v and "No changes?" in v
    assert "Drawing 1 of 2" in v and "All 2 drawings" in v and "engine" not in v.lower().replace("engines", "")
    # old data: a change request made on the earlier portal page (numbered marks in the note) shows as history
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Approval(order_id=o.id, revision_id=rid2, outcome="changes", name="Mr Singh", email=SINGH, how="portal", on="2026-11-27",
                          note="[1] make it 1300 long; [5] plinth lower; also a cable hole", by="Portal: " + SINGH)); s.commit()
    v = c.get("%s/d/%d" % (P, rid2)).text
    assert '<span class="n">1</span>make it 1300 long' in v and '<span class="n">5</span>plinth lower' in v and "also a cable hole" in v
    assert "is with" in v and "come back to you as a new revision" in v and 'id="approve"' not in v      # change with the designer
    # empty send: refused
    r = c.post("%s/d/%d/changes" % (P, rid1), data={"note": "", "marks": "[]"})
    assert "Say what you would like changed" in r.text
    # one send: two marked comments + text + a photo = ONE change request, to the designer (no AI), staff told
    MAIL.clear()
    marks = json.dumps([dict(x=20, y=30, sheet=1, text="handles in black"), dict(x=60, y=40, sheet=1, text="shelf a bit higher")])
    r = c.post("%s/d/%d/changes" % (P, rid1), data={"note": "match the laminate to our sample", "marks": marks},
               files={"file": ("sample.jpg", b"\xff\xd8 not really a jpeg", "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303
    with db.Session() as s:
        ap = s.query(db.Approval).filter_by(revision_id=rid1, outcome="changes").one()
        assert "[1] handles in black" in ap.note and "[2] shelf a bit higher" in ap.note and "match the laminate" in ap.note
        assert s.query(db.Mark).filter(db.Mark.by.like("Client:%")).count() == 2
    assert any(m["subject"].startswith("Changes asked on the portal") for m in MAIL)
    v = c.get("%s/d/%d" % (P, rid1)).text
    assert "handles in black" in v and "match the laminate to our sample" in v and "Photo / file attached" in v
    # Encore replies, then issues Rev B: both in the chat; the Approve Rev B box comes back
    with db.Session() as s:
        s.add(db.ClientAction(order_id=o.id, approval_id=ap.id, kind="reply", text="Thanks, we're on it. You'll get Rev B by email.", by="Neha Sharma")); s.commit()
        l1 = s.query(db.OrderLine).filter_by(order_id=o.id, no=1).one()
        r1 = s.get(db.Revision, rid1)
        s.add(db.Revision(line_id=l1.id, number=r1.number, rev="B", engine_version=r1.engine_version, spec=r1.spec, files=r1.files, issued_by="Neha Sharma")); s.commit()
    ridB = max(i for i, _ in revs().values())
    v = c.get("%s/d/%d" % (P, ridB)).text
    assert "Thanks, we&#39;re on it." in v and "New revision: Rev B" in v and "Approve Rev B" in v and "Neha" in v
    assert v.index("handles in black") < v.index("New revision: Rev B")                   # newest at the bottom
    # approve in the chat with the email code
    c.post(P + "/approve", data={"do": "code", "rid": str(ridB)})
    r = c.post(P + "/approve", data={"do": "approve", "rid": str(ridB), "name": "Mr Singh", "designation": "Admin", "agree": "1",
                                     "code": code_for(SINGH, "Your Encore approval code") if any(m["subject"].startswith("Your Encore approval code") for m in MAIL) else code_for(SINGH)},
               follow_redirects=False)
    assert r.status_code == 303
    v = c.get("%s/d/%d" % (P, ridB)).text
    # approved / locked: no message box, "Approved by … on …"
    assert "Approved by Mr Singh on" in v and 'id="pform"' not in v and 'id="approve"' not in v and "locked for manufacturing" in v
    assert "approved and locked" in c.post("%s/d/%d/changes" % (P, ridB), data={"note": "change it"}).text


def test_only_own_orders():
    """A client with access to one order cannot open another order's drawing page."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    staff.post("/orders/SAMPLE-REPEAT-1/draw")
    with db.Session() as s:
        other = s.query(db.Revision).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref != "Q-2026-10-444").first()
    assert c.get("/portal/orders/SAMPLE-REPEAT-1").status_code == 404
    if other is not None:
        assert c.get("/portal/orders/SAMPLE-REPEAT-1/d/%d" % other.id).status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
