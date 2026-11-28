"""Client requests to add or remove a product (issue #47): only approvers may ask; the request reaches the project's team
in the bell and on the Questions and changes page; Done / Decline need an answer, which the client gets by email and on
the portal; nothing changes by itself."""
import os, sys, tempfile, re
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, issuing

staff, client, viewer = TestClient(app), TestClient(app), TestClient(app)
A = "/orders/Q-2026-10-444"
P = "/portal/orders/Q-2026-10-444"
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))


def sign_in(c, email):
    c.post("/portal/code", data={"email": email})
    m = next(m for m in reversed(MAIL) if m["to"] == [email] and m["subject"].startswith("Your Encore code"))
    c.post("/portal/signin", data={"email": email, "code": re.search(r"(\d{6})", m["subject"]).group(1), "keep": "1"})


def test_requests():
    staff.post("/login/dev/1"); staff.get(A)
    with db.Session() as s:
        o = s.query(db.Order).one()
        l1 = o.lines[0]
        s.add(db.Revision(line_id=l1.id, number="Encore-Nov-2026-05-01", rev="A", spec={"item": {"product_name": "Workstation", "qty": 4}}, files={}))
        s.add(db.PortalAccess(order_id=o.id, email="boss@client.in", name="Mr Singh", can_approve=True))
        s.add(db.PortalAccess(order_id=o.id, email="look@client.in", name="Ms Rao", can_approve=False)); s.commit()
        oid = o.id
    # old orders without requests: no card, nothing changes
    assert 'id="requests"' not in staff.get(A + "/work").text
    sign_in(client, "boss@client.in"); sign_in(viewer, "look@client.in")
    h = client.get(P).text
    assert "Ask to add a product" in h and "Ask to remove a product" in h
    assert "Ask to add a product" not in viewer.get(P).text
    # a view-only email cannot ask, even by sending the form
    assert viewer.post(P + "/request/add", data={"product": "Side table"}, follow_redirects=False).headers["location"].endswith("done=req-view#requests")
    # empty name / nothing ticked: refused
    assert client.post(P + "/request/add", data={"product": "  "}, follow_redirects=False).headers["location"].endswith("done=req-noname#requests")
    assert client.post(P + "/request/remove", data={}, follow_redirects=False).headers["location"].endswith("done=req-none#requests")
    with db.Session() as s:
        assert s.query(db.PortalRequest).count() == 0
    # add (with a photo) and remove part of a product
    client.post(P + "/request/add", data={"product": "Side table 600 x 450", "qty": "2", "note": "same finish"}, files={"file": ("ref.jpg", b"\xff\xd8 img", "image/jpeg")})
    client.post(P + "/request/remove", data={"no": "1", "qty_1": "2", "note": "two seats less"})
    h = client.get(P).text
    assert "Side table 600 x 450" in h and "Product 1 Workstation (2)" in h and h.count("Received") >= 2
    assert any(m["subject"].startswith("Client asks to add a product: Q-2026-10-444") for m in MAIL)      # the salesperson / info@ told
    # the team sees it on the desk and in the bell (admin and the order's salesperson)
    h = staff.get(A + "/work").text
    assert "Client requests" in h and "Side table 600 x 450" in h and "ref.jpg" in h
    with db.Session() as s:
        notes = {(n.user_name, n.source) for n in s.query(db.Notification).filter(db.Notification.resolved_at.is_(None))}
        r1, r2 = [r.id for r in s.query(db.PortalRequest).order_by(db.PortalRequest.id)]
    assert ("Kushal Singhal", "portalreq:%d" % r1) in notes and ("Anchal Dawar", "portalreq:%d" % r1) in notes
    # being handled; Done / Decline need an answer; the client is emailed and sees it; the bell clears
    staff.post("%s/requests/%d/handling" % (A, r1))
    assert staff.post("%s/requests/%d/done" % (A, r1), data={"answer": ""}).status_code == 400
    staff.post("%s/requests/%d/done" % (A, r1), data={"answer": "Added to your quotation Q-2026-10-444 as Product 4."})
    staff.post("%s/requests/%d/declined" % (A, r2), data={"answer": "Those seats are already in production."})
    assert staff.post("%s/requests/%d/declined" % (A, r2), data={"answer": "again"}).status_code == 400          # answered once
    mails = [m for m in MAIL if m["to"] == ["boss@client.in"] and m["subject"].startswith("Your request to")]
    assert len(mails) == 2 and "Added to your quotation" in mails[0]["body"] and "could not make this change" in mails[1]["body"]
    h = client.get(P).text
    assert "Done" in h and "Declined" in h and "Those seats are already in production." in h
    staff.get("/")
    with db.Session() as s:
        assert not s.query(db.Notification).filter(db.Notification.source.like("portalreq:%"), db.Notification.resolved_at.is_(None)).count()
        # nothing changed by itself: still the same products, no quotation change
        assert len(s.query(db.Order).one().lines) == 3 and s.query(db.QuoteChange).count() == 0
        # when the quotation change arrives from Odoo, the card links to it
        s.add(db.QuoteChange(order_id=oid, version=2, kind="added", name="Side table", stage="not drawn")); s.commit()
    assert "The quotation change has come from Odoo" in staff.get(A + "/work").text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
