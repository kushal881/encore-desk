"""Screen 3a: Projects. Stage 1..8 of each project, drawings done / total, what needs attention, stage tiles, tabs,
salesperson filter and search. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile, datetime
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, projects as pj

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def L(answered=True, drawing=None):
    return NS(questions=[NS(answer="x" if answered else "")], drawing=drawing)


def test_project_stage():
    assert pj.project_stage("drawing", []) == 1                                  # nothing to draw yet (empty)
    assert pj.project_stage("drawing", [L(False), L()]) == 2
    assert pj.project_stage("drawing", [L(), L()]) == 3
    assert pj.project_stage("drawing", [L(drawing=NS()), L(False)]) == 2
    assert pj.project_stage("drawing", [L(drawing=NS()), L(drawing=NS())]) == 4
    assert [pj.project_stage(st, [L()]) for st in ("issued", "sent", "approved", "production", "completed")] == [5, 6, 7, 7, 8]


def body(url):
    """The page without the header (the notifications bell also names projects)."""
    return c.get(url).text.split('class="page"', 1)[1]


def row(ref):
    with db.Session() as s:
        return next(r for r in pj.rows(s) if r["ref"] == ref)


def test_projects_page():
    assert c.get("/projects", follow_redirects=False).headers["location"] == "/login"
    c.post("/login/dev/3")                                                          # staff can see every project
    assert "No projects in this view." in c.get("/projects").text
    c.get(A); c.get("/orders/SAMPLE-REPEAT-1")
    with db.Session() as s:                                                         # old data: an order with no salesperson, no folder
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one(); o.salesperson = ""; s.commit()
    h = c.get("/projects").text
    assert "Q-2026-10-444" in h and "SAMPLE-REPEAT-1" in h and "Active projects (2)" in h
    r = row("Q-2026-10-444")
    assert r["st"] == 2 and r["done"] == 0 and r["total"] == 3 and r["folder"] == "" and "questions to answer" in r["flag"]
    assert "2 · Specify" in h
    # filters: stage tile, completed tab, salesperson, search
    assert "Specify · Active projects (2)" in c.get("/projects?stage=2").text
    assert "No projects in this view." in c.get("/projects?stage=5").text
    assert "No projects in this view." in c.get("/projects?show=Completed").text
    sp = row("Q-2026-10-444")["salesperson"]
    if sp:
        h = body("/projects?sp=" + sp)
        assert "Q-2026-10-444" in h and "SAMPLE-REPEAT-1" not in h
    h = body("/projects?q=repeat")
    assert "SAMPLE-REPEAT-1" in h and "Q-2026-10-444" not in h
    assert "No projects in this view." in c.get("/projects?q=nothing-like-this").text
    assert "Active projects" in c.get("/projects?show=bogus&stage=99").text            # wrong values: the default view


def test_stages_and_attention():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2")
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    assert row("Q-2026-10-444")["st"] == 3
    c.post(A + "/draw")
    assert row("Q-2026-10-444")["st"] == 4 and row("Q-2026-10-444")["flag"] == ""
    with db.Session() as s:                                                         # an AI change left waiting
        l3 = s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444", db.OrderLine.no == 3).one()
        s.add(db.AiTurn(line_id=l3.id, typed="600 deep", kind="change", status="trial")); s.commit()
        tid = s.query(db.AiTurn).one().id
    assert row("Q-2026-10-444")["flag"] == "AI change waiting: Accept or Undo"
    c.post("%s/view/3/ai/%d/undo" % (A, tid))
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    r = row("Q-2026-10-444")
    assert r["st"] == 5 and r["done"] == 3 and r["flag"] == "Send the drawings to the client" and r["folder"].endswith("-05")
    c.post(A + "/step/sent", data={"note": "email"})
    with db.Session() as s:                                                         # sent 4 days ago
        st = s.query(db.Step).filter_by(kind="sent").one(); st.at = db.now() - datetime.timedelta(days=4); s.commit()
        rid = s.query(db.Revision).order_by(db.Revision.id).first().id
    assert row("Q-2026-10-444")["st"] == 6 and row("Q-2026-10-444")["flag"] == "Waiting 4 days on client"
    c.post("%s/answer-from-client/%d" % (A, rid), data={"outcome": "changes", "name": "Mr Singh", "how": "phone", "note": "1500 long"})
    assert row("Q-2026-10-444")["flag"] == "Client asked for changes"
    with db.Session() as s:                                                         # approved: the client's answer is replaced
        s.query(db.Approval).delete(); o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Step(order_id=o.id, kind="approved", note="Mr Singh", by="Sushil Singhal")); s.commit()
    assert row("Q-2026-10-444")["st"] == 7
    c.post("/login/dev/1"); c.post(A + "/step/released", data={"note": ""})
    assert row("Q-2026-10-444")["flag"] == "Production set to make"
    with db.Session() as s:
        s.add(db.EngineRequest(order_ref="Q-2026-10-444", product="Product 1", text="glass top", asked_by="Anchal")); s.commit()
    assert row("Q-2026-10-444")["flag"] == "Engine request pending"
    c.post(A + "/step/completed", data={"note": ""})
    assert row("Q-2026-10-444")["st"] == 8
    h = body("/projects?show=Completed")
    assert "Q-2026-10-444" in h and "SAMPLE-REPEAT-1" not in h and "8 · Done" in h
    assert "Q-2026-10-444" in c.get("/projects?show=All").text and "Q-2026-10-444" not in body("/projects")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
