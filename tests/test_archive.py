"""Archive project (issue #50, Kushal's comment): administrator only, at any stage, with a reason; drawings and records
are kept; the project leaves the dashboard, Projects (Active / Completed), Production and the nightly Odoo check, shows
under Projects -> All as Archived, and can be reopened. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, projects as pj

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def body(url):
    return c.get(url).text.split('class="page"', 1)[1]


def test_archive():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(2); c.get(A); c.get("/orders/SAMPLE-REPEAT-1")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    with db.Session() as s:                                            # an AI change left waiting: archiving still works
        l1 = s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444", db.OrderLine.no == 1).one()
        s.add(db.AiTurn(line_id=l1.id, typed="x", kind="change", status="trial")); s.commit()
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue", "nos": "2 3"})      # issued: Remove from the desk is refused
    assert "Active projects (2)" in body("/projects")
    # only the administrator; a reason is needed
    assert c.post(A + "/archive", data={"reason": "test"}).status_code == 403
    assert "Archive this project" not in c.get(A).text
    as_(1)
    assert "Archive this project" in c.get(A).text
    assert c.post(A + "/archive", data={"reason": "  "}).status_code == 400
    c.post(A + "/archive", data={"reason": "test project"})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        assert o.archived_by == "Kushal Singhal" and o.archived_reason == "test project"
        assert len(o.lines) == 3 and s.query(db.Revision).count() == 2           # nothing deleted
    assert c.post(A + "/archive", data={"reason": "again"}).status_code == 400
    # off the active lists and counts; shown under All as Archived
    assert "Q-2026-10-444" not in body("/") and "SAMPLE-REPEAT-1" in body("/")
    assert "Active projects (1)" in body("/projects") and "Q-2026-10-444" not in body("/projects")
    assert "Q-2026-10-444" not in body("/projects?show=Completed")
    h = body("/projects?show=All")
    assert "Q-2026-10-444" in h and "Archived" in h
    with db.Session() as s:
        assert sum(pj.rows(s)[i]["archived"] for i in range(2)) == 1
    v = c.get(A).text
    assert "Archived</b>" in v and "test project" in v and "Reopen project" in v and c.get(A + "/view/2").status_code == 200   # drawings still open
    # reopen: back as it was
    as_(2)
    assert c.post(A + "/reopen").status_code == 403
    as_(1)
    c.post(A + "/reopen")
    assert "Q-2026-10-444" in body("/") and "Active projects (2)" in body("/projects")
    assert c.post(A + "/reopen").status_code == 400                    # not archived any more
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        assert o.archived_at is None and any(m.text.startswith("Project reopened by Kushal Singhal") for m in o.messages)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
