"""Archive project and the client portal (issue #50, Kushal 28-Nov-2026): an archived order disappears from the portal
(Your orders, Past orders, links, drawings); its access list is kept so Reopen restores access; an email with no other
active order can no longer sign in and is signed out at its next click. No email goes to the client. A completed
(not archived) order stays in Past orders. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile, re
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, issuing, portal

A, B = "/orders/Q-2026-10-444", "/orders/SAMPLE-REPEAT-1"
ONLY, BOTH = "singh@achievers.in", "buyer@achievers.in"          # ONLY is on order A alone; BOTH is on A and B
staff = TestClient(app)
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))


def sign_in(c, email):
    c.post("/portal/code", data={"email": email})
    m = next(m for m in reversed(MAIL) if m["to"] == [email] and m["subject"].startswith("Your Encore code"))
    return c.post("/portal/signin", data={"email": email, "code": re.search(r"(\d{6})", m["subject"]).group(1), "keep": "1"}, follow_redirects=False)


def test_archive_hides_from_portal():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    staff.post("/login/dev/1"); staff.get(A); staff.get(B)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        staff.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    staff.post(A + "/draw")
    staff.post(A + "/issue", data={"folder_no": "6", "nos": "1,3", "back": "issue"})       # approved / issued drawings on A
    with db.Session() as s:                                                                # an AI change left waiting on A
        l1 = s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444", db.OrderLine.no == 1).one()
        s.add(db.AiTurn(line_id=l1.id, typed="x", kind="change", status="trial")); s.commit()
    staff.post(A + "/access", data={"email": ONLY, "name": "Mr Singh", "can": "approve"})
    staff.post(A + "/access", data={"email": BOTH, "can": "view"})
    staff.post(B + "/access", data={"email": BOTH, "can": "view"})
    with db.Session() as s:
        rid = s.query(db.Revision).first().id
        oid = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().id
    link = portal.link_for(ONLY, oid, "http://t")

    c1, c2 = TestClient(app), TestClient(app)
    assert sign_in(c1, ONLY).status_code == 303 and sign_in(c2, BOTH).status_code == 303
    assert "Q-2026-10-444" in c1.get("/portal/orders").text and c1.get("/portal/orders/Q-2026-10-444").status_code == 200

    # the Archive box names who loses access: ONLY (A is their only order), not BOTH
    v = staff.get(A).text
    assert "will lose portal access" in v and ONLY in v.split("will lose portal access", 1)[1].split("</div>", 1)[0]
    assert BOTH not in v.split("will lose portal access", 1)[1].split("</div>", 1)[0]
    sent = len(MAIL)
    staff.post(A + "/archive", data={"reason": "test project"})
    assert len(MAIL) == sent                                                              # no email to the client
    with db.Session() as s:
        o = s.get(db.Order, oid)
        assert any("portal access ended for " + ONLY in m.text for m in o.messages)
        assert s.query(db.PortalAccess).filter_by(order_id=oid).count() == 2              # the list is kept, not deleted

    # BOTH still signed in, sees B only; A's pages and drawings are gone
    h = c2.get("/portal/orders").text
    assert "SAMPLE-REPEAT-1" in h and "Q-2026-10-444" not in h and "Q-2026-10-444" not in c2.get("/portal/past").text
    r = c2.get("/portal/orders/Q-2026-10-444")
    assert r.status_code == 404 and "no longer available" in r.text
    assert c2.get("/portal/orders/Q-2026-10-444/d/%d/pdf" % rid).status_code == 404
    # ONLY is signed out at the next click, cannot get a code, and the old email link says it's gone
    r = c1.get("/portal/orders", follow_redirects=False)
    assert r.status_code in (302, 303) and "/portal/login" in r.headers["location"]
    n = len(MAIL); c1.post("/portal/code", data={"email": ONLY}); assert len(MAIL) == n
    r = TestClient(app).get(link.replace("http://t", ""), follow_redirects=False)
    assert "error=gone" in r.headers["location"]
    assert "no longer available" in TestClient(app).get("/portal/login?error=gone").text

    # Reopen: the same people get access again
    staff.post(A + "/reopen")
    assert sign_in(c1, ONLY).status_code == 303 and "Q-2026-10-444" in c1.get("/portal/orders").text
    assert "will lose portal access" in staff.get(A).text                                 # box back, list computed again


def test_completed_stays_in_past_orders():
    """Completed (not archived) orders stay in Past orders; old data with no archive fields counts as active."""
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        assert o.archived_at is None
        s.add(db.Step(order_id=o.id, kind="completed", by="test")); s.commit()
    c = TestClient(app)
    assert sign_in(c, BOTH).status_code == 303
    assert "SAMPLE-REPEAT-1" in c.get("/portal/past").text


def test_empty_values():
    assert portal.losing_access(0) == [] and portal.listed("") == [] and portal.listed(None) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
