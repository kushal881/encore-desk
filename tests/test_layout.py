"""The shared layout: side menu with the Encore logo on every page, Back button, and Engine requests for administrators only. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app

c = TestClient(app)
A = "/orders/Q-2026-10-444"
LOGO = 'src="/static/encore-logo.png"'
MENU_ENGINE = 'href="/engine-requests" class="item'


def test_logo_file_and_sign_in_page():
    r = c.get("/static/encore-logo.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert LOGO in c.get("/login").text


def test_staff_layout():
    c.post("/logout"); c.post("/login/dev/2")                                    # Sushil, not the administrator
    for url in ("/", A, A + "/work"):
        h = c.get(url).text
        assert 'class="sidenav"' in h and LOGO in h and "Sign out" in h and "Sushil Singhal" in h, url
        assert MENU_ENGINE not in h and "Engine requests waiting" not in h, url    # engine requests are for the administrator only
    assert "Back to the previous page" not in c.get("/").text                    # no Back on the dashboard itself
    assert "Back to the previous page" in c.get(A).text


def test_admin_layout():
    c.post("/logout"); c.post("/login/dev/1")                                    # Kushal, the administrator
    h = c.get("/").text
    assert MENU_ENGINE in h and "Engine requests waiting" in h
    assert 'class="item on"' in h                                                # Dashboard marked as the current page
    h = c.get("/engine-requests").text
    assert 'class="sidenav"' in h and "Back to the previous page" in h
