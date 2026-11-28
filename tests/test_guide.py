"""The Guide in the desk (issue #42): staff see the Staff Guide only, the administrators both; the Directors Guide is
refused for staff also by address; a missing guide file is a friendly message; every "?" points at a section that exists;
the dashboard card can be hidden per person."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, guide

c = TestClient(app)


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def test_render():
    h, toc = guide.render("# Title\n\n## 2. Start a project\n\nSome **bold** and `code` <script>x</script>.\n\n1. one\n2. two\n\n- a\n- b\n\n| A | B |\n|---|---|\n| 1 | **2** |\n")
    assert toc == [("start-a-project", "2. Start a project")] and "id='start-a-project'" in h
    assert "<b>bold</b>" in h and "<code>code</code>" in h and "<script>" not in h and "&lt;script&gt;" in h
    assert "<ol><li>one</li><li>two</li></ol>" in h.replace("\n", "") and "<ul><li>a</li><li>b</li></ul>" in h.replace("\n", "")
    assert "<th>A</th>" in h and "<td><b>2</b></td>" in h and "---" not in h
    assert guide.render("") == ("", [])


def test_every_help_link_exists():
    for page, (which, anchor) in guide.HELP.items():
        body, toc = guide.load(which)
        assert body and anchor in [a for a, _ in toc], (page, which, anchor)


def test_pages():
    as_(3)                                                              # staff (sales coordinator)
    h = c.get("/guide").text
    assert "Getting started" in h and "Contents" in h and "Directors Guide" not in h
    assert c.get("/guide/directors").status_code == 403 and c.get("/guide/nope").status_code == 404
    assert 'href="/guide" class="item' in c.get("/").text
    assert "New here?" in c.get("/").text
    c.post("/guide/dismiss")
    assert "New here?" not in c.get("/").text                           # hidden for this person only
    h = c.get("/new").text
    assert 'href="/guide/staff#start-a-project"' in h                    # the "?" next to the title
    as_(1)                                                              # the administrator: both guides
    assert "Directors Guide" in c.get("/guide").text and "Approvals" in c.get("/guide/directors").text
    assert "Directors guide" in c.get("/").text                         # the card points admins at their guide
    assert 'href="/guide/directors#approvals"' in c.get("/approvals").text
    as_(2)                                                              # the secondary administrator reads it too
    assert c.get("/guide/directors").status_code == 200


def test_missing_file(monkeypatch):
    monkeypatch.setattr(guide, "DOCS", tempfile.mkdtemp())
    as_(1)
    r = c.get("/guide")
    assert r.status_code == 200 and "is not available right now" in r.text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
