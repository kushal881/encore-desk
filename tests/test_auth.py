"""Google sign-in rules, with Google's answer faked."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["GOOGLE_CLIENT_ID"] = "id"; os.environ["GOOGLE_CLIENT_SECRET"] = "secret"
os.environ.pop("DESK_DEV_LOGIN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import auth, db


def _try(identity, tamper_state=False):
    c = TestClient(app)
    r = c.get("/auth/google", follow_redirects=False)
    assert r.headers["location"].startswith("https://accounts.google.com/") and "hd=encoreprimefurniture.com" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    auth.google_identity = lambda code, uri: identity
    r = c.get("/auth/callback?code=x&state=" + ("wrong" if tamper_state else state), follow_redirects=False)
    return c, r.headers["location"]


def test_sign_in_rules():
    c = TestClient(app)
    assert c.post("/login/dev/1", follow_redirects=False).status_code == 404                       # no pick-a-user without the building flag
    c, to = _try(dict(email="Sushil@encoreprimefurniture.com", email_verified=True))
    assert to == "/" and c.get("/orders/Q-2026-10-444").status_code == 200
    assert _try(dict(email="someone@encoreprimefurniture.com", email_verified=True))[1] == "/login?error=notlisted"   # right company, not on the list
    assert _try(dict(email="kushal@gmail.com", email_verified=True))[1] == "/login?error=notlisted"                    # outside the company
    assert _try(dict(email="kushal@encoreprimefurniture.com", email_verified=False))[1] == "/login?error=notlisted"   # unverified address
    assert _try(dict(email="kushal@encoreprimefurniture.com", email_verified=True), tamper_state=True)[1] == "/login?error=failed"
    c, to = _try(dict(email="info@encoreprimefurniture.com", email_verified=True))
    assert "Anchal Dawar · Sales coordinator" in c.get("/orders/Q-2026-10-444").text
    with db.Session() as s:                                                                        # removing someone takes effect at once
        u = s.query(db.User).filter_by(email="info@encoreprimefurniture.com").one(); u.active = False; s.commit()
    assert c.get("/orders/Q-2026-10-444", follow_redirects=False).headers["location"] == "/login"


if __name__ == "__main__":
    test_sign_in_rules(); print("auth tests pass")
