"""Acknowledgement emails (issue #51, R3): a client who asks for changes or a reorder on the portal gets one email from
info@ with a copy of their notes; only the person who asked; a failed email never stops the request; nothing is sent
again for requests made before."""
import os, sys, tempfile, re, json
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, issuing

staff, client = TestClient(app), TestClient(app)
A, P = "/orders/Q-2026-10-444", "/portal/orders/Q-2026-10-444"
MAIL, FAIL = [], {"on": False}


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    def send(to, cc, subject, body, att=None, name=None):
        if FAIL["on"] and not subject.startswith("Your Encore code"):
            raise RuntimeError("Email is not set up yet")
        MAIL.append(dict(to=to, subject=subject, body=body))
    monkeypatch.setattr(issuing, "send", send)


def acks():
    return [m for m in MAIL if m["subject"].startswith("We received your")]


def test_ack():
    staff.post("/login/dev/1"); staff.get(A)
    with db.Session() as s:
        o = s.query(db.Order).one()
        l = o.lines[0]
        r = db.Revision(line_id=l.id, number="Encore-Nov-2026-05-01", rev="A", spec={"item": {"product_name": "Workstation", "product_code": "1200 x 600", "qty": 4}}, files={})
        s.add(r); s.flush(); rid = r.id
        s.add(db.PortalAccess(order_id=o.id, email="boss@client.in", name="Mr Singh", can_approve=True))
        s.add(db.PortalAccess(order_id=o.id, email="cc@client.in", name="Ms Rao", can_approve=False))
        # old data: a change request from before this feature; nothing is sent for it
        s.add(db.Approval(order_id=o.id, revision_id=rid, outcome="changes", name="Mr Singh", email="boss@client.in", how="portal", note="[1] old"))
        s.commit()
    client.post("/portal/code", data={"email": "boss@client.in"})
    code = re.search(r"(\d{6})", next(m for m in reversed(MAIL) if m["to"] == ["boss@client.in"])["subject"]).group(1)
    client.post("/portal/signin", data={"email": "boss@client.in", "code": code, "keep": "1"})
    assert not acks()
    client.post("%s/d/%d/changes" % (P, rid), data={"marks": json.dumps([{"x": 40, "y": 20, "sheet": 1, "text": "Increase size to 1050mm"}]),
                                                    "note": "and a grommet", "name": "Mr Singh"})
    a = acks()
    assert len(a) == 1 and a[0]["to"] == ["boss@client.in"]                                 # once, to the person who asked only
    assert a[0]["subject"] == "We received your changes: Q-2026-10-444 · Encore-Nov-2026-05-01 Rev A"
    assert "[1] Increase size to 1050mm" in a[0]["body"]
    assert "Other: and a grommet" in a[0]["body"] and "will not be manufactured" in a[0]["body"]
    # a failed email never stops the request; the history says so
    FAIL["on"] = True
    client.post("%s/d/%d/changes" % (P, rid), data={"marks": "[]", "note": "one more change"})
    FAIL["on"] = False
    with db.Session() as s:
        assert s.query(db.Approval).filter_by(note="one more change").count() == 1
        assert any("could not be emailed" in m.text for m in s.query(db.Message))
    assert len(acks()) == 1
    # a reorder on a past order: its own acknowledgement, to the person who asked
    with db.Session() as s:
        o = s.query(db.Order).one()
        s.add(db.Step(order_id=o.id, kind="approved", note="ok", by="x")); s.add(db.Step(order_id=o.id, kind="completed", note="", by="x")); s.commit()
        l1 = o.lines[0].no
    client.post(P + "/reorder", data={"no": str(l1), "qty_%d" % l1: "5", "note": "same finish"})
    a = acks()
    assert len(a) == 2 and a[1]["to"] == ["boss@client.in"] and a[1]["subject"] == "We received your reorder request: Q-2026-10-444"
    assert "Workstation (1200 x 600), quantity 5" in a[1]["body"] and "not an order until you confirm" in a[1]["body"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
