"""Issue #42: the Guide in the desk. Staff see the Staff Guide; the administrator and the secondary administrator also see
the Directors Guide; the dashboard card can be hidden per person; every '?' help link opens a section that exists."""
import os, sys, tempfile, re, sqlite3
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, guide

c = TestClient(app)
A = "/orders/Q-2026-10-444"
STAFF, ADMIN, ADMIN2 = 3, 1, 2           # Anchal (coordinator), Kushal (administrator), Sushil (secondary administrator)


def login(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def test_staff_sees_only_the_staff_guide():
    assert c.get("/guide", follow_redirects=False).headers["location"] == "/login"             # signed out
    login(STAFF)
    h = c.get("/guide").text
    assert "Staff Guide" in h and 'id="s1"' in h
    assert "Contents" in h and 'href="#s5"' in h and "Source of truth" not in h
    assert "Directors Guide</a>" not in h
    assert c.get("/guide/directors").status_code == 403
    assert 'href="/guide" class="item' in c.get("/").text                                        # menu item on every page
    assert 'href="/guide" class="item' in c.get(A).text


@pytest.mark.parametrize("uid", [ADMIN, ADMIN2])
def test_admins_see_both(uid):
    login(uid)
    assert "Directors Guide</a>" in c.get("/guide").text
    h = c.get("/guide/directors").text
    assert "Who can do what" in h and "<table>" in h and 'id="s3"' in h                             # tables rendered


def test_every_help_link_points_at_a_section():
    have = {g: {a for a, _ in guide.render(g)[1]} for g in guide.GUIDES}
    for _, g, sec, _ in guide.HELP:
        assert sec in have[g], (g, sec)
    assert guide.help_for("/orders/Q-1/issue")["url"] == "/guide#s5"
    assert guide.help_for("/approvals") is None and guide.help_for("/approvals", True)["url"] == "/guide/directors#s3"
    assert guide.help_for("") is None and guide.help_for(None) is None                            # empty values
    login(STAFF)
    c.get(A)
    assert 'href="/guide#s5" class="helpq"' in c.get(A + "/issue").text
    assert 'class="helpq"' in c.get("/my-requests").text
    login(ADMIN)
    assert 'href="/guide/directors#s3" class="helpq"' in c.get("/approvals").text


def test_missing_file_is_a_friendly_message(monkeypatch):
    monkeypatch.setattr(guide, "DOCS", "/nowhere")
    login(STAFF)
    r = c.get("/guide")
    assert r.status_code == 200 and "The guide is not available just now" in r.text


def test_dashboard_card_hidden_per_person():
    login(STAFF)
    assert "Read the guide ›" in c.get("/").text
    c.post("/guide/dismiss")
    assert "Read the guide ›" not in c.get("/").text
    login(ADMIN)
    assert "Directors guide ›" in c.get("/").text                                                 # someone else still sees theirs


def test_old_user_table_gets_the_column(tmp_path):
    """Old data: a user table from an earlier desk has no guide_dismissed; the upgrade adds it, off."""
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE user_ (id INTEGER PRIMARY KEY, email VARCHAR(200) UNIQUE, name VARCHAR(120), role VARCHAR(20), active BOOLEAN)")
    con.execute("INSERT INTO user_ VALUES (1, 'a@encoreprimefurniture.com', 'A', 'drawing', 1)")
    con.commit(); con.close()
    from sqlalchemy import create_engine, text
    eng = create_engine("sqlite:///%s" % path)
    assert "user_.guide_dismissed" in db.upgrade(eng)
    with eng.connect() as k:
        assert not k.execute(text("SELECT guide_dismissed FROM user_")).scalar()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
