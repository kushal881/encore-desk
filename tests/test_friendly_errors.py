"""Browsers get readable pages instead of raw JSON errors (Kushal, 28-Nov-2026): typing a client name in the dashboard's
quotation box searches New project; an empty answer explains itself; JSON stays for scripts and the Odoo webhook."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app

c = TestClient(app)
HTML = {"accept": "text/html,application/xhtml+xml"}


def test_friendly_errors():
    c.post("/login/dev/1")
    # a name instead of a number: goes to New project's search, not an error
    r = c.post("/open", data={"ref": "sudeva"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/new?q=sudeva"
    assert c.post("/open", data={"ref": "  "}, follow_redirects=False).headers["location"] == "/"
    # unknown number in a browser: a page with a way back, not JSON
    r = c.get("/orders/Q-0000-00-000", headers=HTML)
    assert r.status_code == 404 and "<html" in r.text and "Go back" in r.text and '"detail"' not in r.text
    # empty answer in a browser: readable, not a 422 JSON list
    r = c.post("/orders/Q-0000-00-000/answer/1", data={}, headers=HTML)
    assert "<html" in r.text and '"detail"' not in r.text
    # scripts / fetch calls still get JSON
    r = c.get("/orders/Q-0000-00-000")
    assert r.status_code == 404 and r.json()["detail"]
