"""Users & roles (issue #40): administrator only; add an @encoreprimefurniture.com person, change a role, switch off / on,
never delete; every change logged; the secondary administrator becomes a role of its own; last sign-in shown."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, auth

c = TestClient(app)


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def uid(email):
    with db.Session() as s:
        return s.query(db.User).filter_by(email=email).one().id


def test_users():
    with db.Session() as s:                                             # old data: Sushil was 'drawing' + listed as secondary admin
        su = s.query(db.User).filter_by(email="sushil@encoreprimefurniture.com").one()
        assert su.role == "admin2" and auth.level(su) == "admin2"
        assert s.query(db.UserLog).filter_by(user_id=su.id).count() == 1
    # only the administrator; the secondary administrator and staff are refused
    for who in (2, 3):
        as_(who)
        assert c.get("/users").status_code == 403 and c.post("/users/add", data={"email": "x@encoreprimefurniture.com", "name": "X"}).status_code == 403
        assert 'href="/users"' not in c.get("/").text
    as_(1)
    h = c.get("/users").text
    assert "Users &amp; roles" in h and "Secondary administrator" in h and "Sushil Singhal" in h and 'href="/users"' in h
    # add: empty name, wrong domain, duplicate refused; a good one is added and logged
    assert "Enter the person" in c.post("/users/add", data={"email": "twinkle@encoreprimefurniture.com", "name": "  "}).text
    assert "must be an @encoreprimefurniture.com address" in c.post("/users/add", data={"email": "twinkle@gmail.com", "name": "Twinkle"}).text
    assert "must be an @encoreprimefurniture.com address" in c.post("/users/add", data={"email": "@encoreprimefurniture.com", "name": "Twinkle"}).text
    c.post("/users/add", data={"email": "Twinkle@EncorePrimeFurniture.com", "name": "Twinkle", "role": "sales"})
    assert "already on the list" in c.post("/users/add", data={"email": "twinkle@encoreprimefurniture.com", "name": "T2"}).text
    t = uid("twinkle@encoreprimefurniture.com")
    with db.Session() as s:
        assert s.get(db.User, t).role == "sales"
    # change role, switch off and on; never deleted; all logged
    c.post("/users/%d/role" % t, data={"role": "coordinator"})
    c.post("/users/%d/off" % t)
    as_(t)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"         # switched off: refused at once
    as_(1)
    c.post("/users/%d/on" % t)
    with db.Session() as s:
        u = s.get(db.User, t)
        assert u.role == "coordinator" and u.active
        log = [x.text for x in s.query(db.UserLog).filter_by(user_id=t).order_by(db.UserLog.id)]
    assert log == ["Added Twinkle (twinkle@encoreprimefurniture.com) as Salesperson", "Role of Twinkle changed from Salesperson to Sales coordinator",
                   "Twinkle switched off (cannot sign in)", "Twinkle switched on"]
    assert "Pick a role" in c.post("/users/%d/role" % t, data={"role": "boss"}).text                # unknown role refused
    # the administrator cannot lock themselves out
    assert "cannot switch yourself off" in c.post("/users/1/off").text
    assert "cannot switch yourself off" in c.post("/users/1/role", data={"role": "drawing"}).text
    # old role names still sign in; last sign-in is recorded
    as_(3)
    assert c.get("/").status_code == 200
    with db.Session() as s:
        assert s.get(db.User, 3).last_sign_in is not None
    with db.Session() as s:                                             # someone added but never signed in shows "never"
        s.add(db.User(email="new@encoreprimefurniture.com", name="New person", role="drawing")); s.commit()
    as_(1)
    row = c.get("/users").text.split("<b>New person</b>")[1].split("</tr>")[0]
    assert "never" in row


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
