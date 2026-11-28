"""Staff invitations (issue #67): a tick on Add (on by default) sends the invitation; Send invite / Send again per active
person who has not signed in; never for switched-off people; administrator only; logged in Changes; an email failure
never blocks adding; old users (no invitation record) show the button."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
os.environ["DESK_URL"] = "https://portal.encoreprimefurniture.com"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, issuing

c = TestClient(app)
MAIL = []


@pytest.fixture
def mail(monkeypatch):
    MAIL.clear()
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def person(email):
    with db.Session() as s:
        return s.query(db.User).filter_by(email=email).one()


def test_invite_on_add(mail):
    as_(1)
    r = c.post("/users/add", data={"name": "Ravi Kumar", "email": "ravi@encoreprimefurniture.com", "role": "sales", "invite": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    m = MAIL[-1]
    assert m["to"] == ["ravi@encoreprimefurniture.com"] and m["subject"] == "Your access to the Encore drawing desk"
    assert "Dear Ravi Kumar" in m["body"] and "as Salesperson: projects, quotation changes" in m["body"]
    assert "https://portal.encoreprimefurniture.com" in m["body"] and "Sign in with Google" in m["body"] and "ravi@encoreprimefurniture.com account" in m["body"]
    assert "Staff Guide" in m["body"] and "Directors Guide" not in m["body"]
    assert person("ravi@encoreprimefurniture.com").invited_at is not None
    h = c.get("/users").text
    assert "Invited " in h and "Invitation sent to Ravi Kumar" in h and "Send again" in h
    # unticked: added, no email
    n = len(MAIL)
    c.post("/users/add", data={"name": "Meena", "email": "meena@encoreprimefurniture.com", "role": "drawing"})
    assert len(MAIL) == n and person("meena@encoreprimefurniture.com").invited_at is None
    # an administrator's invitation names the Directors Guide
    c.post("/users/add", data={"name": "Asha", "email": "asha@encoreprimefurniture.com", "role": "admin2", "invite": "1"})
    assert "Directors Guide" in MAIL[-1]["body"]


def test_send_invite_button(mail):
    as_(1)
    h = c.get("/users").text
    meena = person("meena@encoreprimefurniture.com")
    assert '/users/%d/invite' % meena.id in h                                            # old record, never invited: the button shows
    assert '/users/1/invite' not in h                                                    # Kushal has signed in: no button
    c.post("/users/%d/invite" % meena.id)
    assert MAIL[-1]["to"] == ["meena@encoreprimefurniture.com"] and person("meena@encoreprimefurniture.com").invited_at
    r = c.post("/users/%d/invite" % meena.id, follow_redirects=False)                    # a reminder
    assert "again" in r.headers["location"] and len(MAIL) == 2
    # switched off: no button, refused
    c.post("/users/%d/off" % meena.id)
    assert '/users/%d/invite' % meena.id not in c.get("/users").text
    n = len(MAIL)
    r = c.post("/users/%d/invite" % meena.id, follow_redirects=False)
    assert "error=" in r.headers["location"] and len(MAIL) == n
    # someone who has signed in: refused
    r = c.post("/users/1/invite", follow_redirects=False)
    assert "already+signed+in" in r.headers["location"].replace("%20", "+") and len(MAIL) == n


def test_not_admin_refused(mail):
    as_(2)                                                                               # secondary administrator
    assert c.post("/users/%d/invite" % person("ravi@encoreprimefurniture.com").id).status_code == 403
    as_(3)
    assert c.post("/users/add", data={"name": "X", "email": "x@encoreprimefurniture.com", "invite": "1"}).status_code == 403
    assert MAIL == []


def test_email_failure_does_not_block_adding(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("Email is not set up yet: add SMTP_PASSWORD")
    monkeypatch.setattr(issuing, "send", broken)
    as_(1)
    r = c.post("/users/add", data={"name": "Dev", "email": "dev@encoreprimefurniture.com", "role": "drawing", "invite": "1"}, follow_redirects=False)
    p = person("dev@encoreprimefurniture.com")
    assert p.active and p.invited_at is None
    h = c.get(r.headers["location"]).text
    assert "Added, but the invitation could not be sent: Email is not set up yet" in h
    assert "Invitation sent to Dev" not in h


def test_empty_values():
    """An empty name or a wrong address is refused before any email."""
    as_(1)
    assert "error=" in c.post("/users/add", data={"name": "", "email": "a@encoreprimefurniture.com", "invite": "1"}, follow_redirects=False).headers["location"]
    assert "error=" in c.post("/users/add", data={"name": "A", "email": "a@gmail.com", "invite": "1"}, follow_redirects=False).headers["location"]
    assert c.post("/users/999/invite").status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
