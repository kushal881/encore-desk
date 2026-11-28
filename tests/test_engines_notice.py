"""Issue #38: the desk checks the approved repository by itself and tells administrators when a newer approved engine
version is ready ("Engines v17 is approved and ready - Update now"): a dashboard banner and a popup that can be closed
until the next version. Non-administrators see nothing; a failed check shows nothing. The repository is replaced by a
stand-in, so no network is used."""
import os, sys, tempfile, sqlite3
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

c = TestClient(app)
BANNER, POPUP = "is approved and ready.", 'aria-label="New engines version"'


@pytest.fixture
def repo(monkeypatch):
    """The approved repository: newest tag in state["tag"]; None = it cannot be read."""
    state = {"tag": "v16"}

    def latest():
        if state["tag"] is None:
            raise RuntimeError("GitHub could not be reached")
        return state["tag"]
    monkeypatch.setattr(engines, "latest_tag", latest)
    monkeypatch.setattr(engines, "VERSION", "v16")
    monkeypatch.setattr(engines, "NEWER", "")
    return state


def test_check_rules(repo, capsys):
    assert engines.check_newer() == "" and engines.NEWER == ""                 # no newer version
    repo["tag"] = "v17"
    assert engines.check_newer() == "v17"
    repo["tag"] = None                                                         # the check fails: nothing shown, only logged
    assert engines.check_newer() == "" and "could not check" in capsys.readouterr().out
    repo["tag"] = "v15"                                                        # never offers an older version
    assert engines.check_newer() == ""
    engines.VERSION = "developer copy"                                         # a developer's own copy: never a notice
    repo["tag"] = "v99"
    assert engines.check_newer() == ""
    engines.VERSION = "not available (ENGINES_KEY is not set)"                 # empty / missing version in use
    assert engines.check_newer() == ""
    assert engines._num("") is None and engines._num(None) is None and engines._num("v7") == 7


def test_notice_for_administrators_only(repo, monkeypatch):
    c.post("/login/dev/1")                                                     # Kushal, administrator
    h = c.get("/").text
    assert BANNER not in h and POPUP not in h and "checks for a newer approved version every hour" in h
    assert "Check for a new approved version" not in h                          # the old button is gone

    repo["tag"] = "v17"; engines.check_newer()
    h = c.get("/").text
    assert "Engines v17 is approved and ready." in h and POPUP in h and 'action="/engines/refresh"' in h
    assert POPUP in c.get("/orders/Q-2026-10-444").text                         # the popup shows on any page

    c.post("/logout"); c.post("/login/dev/2")                                  # a drawing user: nothing
    h = c.get("/").text
    assert BANNER not in h and POPUP not in h and "v17" not in h
    c.post("/engines/dismiss")
    with db.Session() as s:
        assert s.get(db.User, 2).engines_dismissed == ""
    assert c.post("/engines/refresh").status_code == 403

    # closed by the administrator: the popup stays away, the banner stays, until the next version
    c.post("/logout"); c.post("/login/dev/1")
    r = c.post("/engines/dismiss", headers={"referer": "http://testserver/orders/Q-2026-10-444"}, follow_redirects=False)
    assert r.headers["location"] == "/orders/Q-2026-10-444"
    assert c.post("/engines/dismiss", headers={"referer": "https://evil.example//x"}, follow_redirects=False).headers["location"] == "/"
    h = c.get("/").text
    assert POPUP not in h and "Engines v17 is approved and ready." in h
    repo["tag"] = "v18"; engines.check_newer()
    assert POPUP in c.get("/").text and "Engines v18" in c.get("/").text

    # the check fails later: nothing shown, no error
    repo["tag"] = None; engines.check_newer()
    h = c.get("/").text
    assert BANNER not in h and POPUP not in h

    # Update now: switches to the new version; the notice goes; issued drawings keep their version
    with db.Session() as s:
        o = db.Order(client=db.Client(name="Old Client"), source="sample", source_ref="OLD-1")
        l, l2 = db.OrderLine(no=1, name="Desk", qty=1), db.OrderLine(no=2, name="Pedestal", qty=1)
        o.lines += [l, l2]; s.add(o); s.flush()
        s.add(db.Revision(line_id=l.id, number="Encore-Nov-2026-07-01", rev="A", engine_version="v16", files={}))
        s.add(db.AiTurn(line_id=l2.id, typed="make it 1500", kind="change", status="trial", engine_version="v16"))   # an AI change waiting
        s.add(db.Approval(order_id=o.id, revision_id=1, outcome="approved", name="Client", how="email"))            # an approved drawing
        s.commit()
    repo["tag"] = "v17"; engines.check_newer()
    monkeypatch.setattr(engines, "_download", lambda v: "/engines/" + v)
    r = c.post("/engines/refresh", follow_redirects=False)
    assert r.status_code == 303 and engines.VERSION == "v17" and engines.NEWER == ""
    h = c.get("/").text
    assert BANNER not in h and POPUP not in h and "v17" in h
    with db.Session() as s:
        assert s.query(db.Revision).filter_by(number="Encore-Nov-2026-07-01").one().engine_version == "v16"
        assert s.query(db.AiTurn).one().status == "trial"
    # a failed update says why, and changes nothing
    repo["tag"] = None
    assert c.post("/engines/refresh").status_code == 502 and engines.VERSION == "v17"


def test_old_user_table_gets_the_new_column(tmp_path):
    """Old data: a user table made by an earlier desk has no engines_dismissed; the upgrade adds it, empty."""
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE user_ (id INTEGER PRIMARY KEY, email VARCHAR(200) UNIQUE, name VARCHAR(120), role VARCHAR(20), active BOOLEAN)")
    con.execute("INSERT INTO user_ VALUES (1, 'kushal@encoreprimefurniture.com', 'Kushal Singhal', 'admin', 1)")
    con.commit(); con.close()
    from sqlalchemy import create_engine, text
    eng = create_engine("sqlite:///%s" % path)
    assert "user_.engines_dismissed" in db.upgrade(eng)
    with eng.connect() as k:
        assert k.execute(text("SELECT engines_dismissed FROM user_")).scalar() == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
