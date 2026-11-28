"""First-time guided tour (issue #43): on the first sign-in only; finishing or skipping is remembered on the server; the
replay link brings it back; the desk tour differs for administrators, staff and production; clients get their own tour
once per email, with a replay link in the footer."""
import os, sys, tempfile, re, json
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, issuing, tour

c = TestClient(app)
MAIL = []


def steps(html):
    m = re.search(r"startTour\((\[.*?\]),\"", html, re.S)
    return [s["title"] for s in json.loads(m.group(1))] if m else None


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def test_desk_tour():
    as_(3)                                                             # staff
    s = steps(c.get("/").text)
    assert s and "The menu" in s and "Approvals" not in s
    assert steps(c.get("/").text)                                       # still there until finished or skipped
    c.post("/tour/done")
    assert steps(c.get("/").text) is None                               # remembered on the server
    assert steps(c.get("/?tour=1").text)                                # Take the tour again
    as_(1)                                                             # the administrator: two more steps
    s = steps(c.get("/").text)
    assert "Approvals" in s and "Engines" in s and len(s) == len(tour.STAFF) + 2
    with db.Session() as d:                                            # production: its own tour on its own page
        d.add(db.User(email="production@encoreprimefurniture.com", name="Production", role="production")); d.commit()
        pid = d.query(db.User).filter_by(role="production").one().id
    as_(pid)
    s = steps(c.get("/production").text)
    assert s and s[0] == "Production"
    assert 'href="/production?tour=1"' in c.get("/guide").text


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject)))


def test_portal_tour(mail):
    as_(1); c.get("/orders/Q-2026-10-444")
    with db.Session() as d:
        o = d.query(db.Order).first()
        d.add(db.PortalAccess(order_id=o.id, email="boss@client.in", name="Mr Singh", can_approve=True)); d.commit()
    p = TestClient(app)
    p.post("/portal/code", data={"email": "boss@client.in"})
    code = re.search(r"(\d{6})", next(m for m in reversed(MAIL) if m["to"] == ["boss@client.in"])["subject"]).group(1)
    p.post("/portal/signin", data={"email": "boss@client.in", "code": code, "keep": "1"})
    h = p.get("/portal/orders").text
    assert steps(h) and steps(h)[0] == "Your orders" and "Take the tour again" in h
    p.post("/portal/tour/done")
    assert steps(p.get("/portal/orders").text) is None
    assert steps(p.get("/portal/orders?tour=1").text)
    # no desk wording on the portal tour
    assert not any(w in json.dumps(tour.PORTAL).lower() for w in ("desk", "engine", "issue"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
