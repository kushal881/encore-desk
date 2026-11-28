"""Order checklist (issue #44): numbered steps from new project to done, worked out from what the order holds; shown on
the project page, in Projects ("Step 9/15") and as "My next steps" on the dashboard. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
import desk.app as appmod
from desk.app import app
from desk import db, engines, checklist as cl

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def st():
    items = cl.steps(appmod.load("Q-2026-10-444"))
    return {x["title"].split(":")[0].split(" (")[0]: x for x in items}, items


def test_checklist():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    s, items = st()
    assert [x["no"] for x in items] == list(range(1, len(items) + 1))
    assert s["Project created from the Odoo quotation"]["state"] == "done" and s["All questions answered"]["state"] == "now"
    assert "4 left" in s["All questions answered"]["note"] and s["Areas set"]["state"] == "optional"
    assert "Quotation changes all handled" not in s                     # only shown when there are some
    h = c.get(A).text
    assert "Order checklist" in h and "Step 3 of %d · All questions answered" % len(items) in h
    with db.Session() as d:
        qs = {q.key: q.id for q in d.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    s, _ = st()
    assert s["All previews drawn"]["state"] == "done" and s["All previews drawn"]["who"] == "Sushil Singhal"
    # an AI change waiting: step 6 blocked with the reason
    with db.Session() as d:
        l1 = d.query(db.OrderLine).filter_by(no=1).one()
        d.add(db.AiTurn(line_id=l1.id, typed="1500", kind="change", status="trial")); d.commit()
        tid = d.query(db.AiTurn).one().id
    s, _ = st()
    six = s["All drawings reviewed"]
    assert six["state"] == "blocked" and six["block"] == "Product 1: Accept or Undo the change first"
    assert "Product 1: Accept or Undo the change first" in c.get("/").text                     # My next steps
    c.post("%s/view/1/ai/%d/undo" % (A, tid))
    for no in (1, 2, 3):                                                   # mark each preview ready
        v = c.get("%s/view/%d" % (A, no)).text
        import re
        key = re.search(r'name="shown" value="(d\d+)"', v)
        c.post("%s/view/%d/ready" % (A, no), data={"shown": key.group(1) if key else ""})
    s, _ = st()
    assert s["All drawings reviewed"]["state"] == "done" and s["Client added to the portal access list"]["state"] == "now"
    c.post(A + "/access", data={"email": "singh@achievers.in", "name": "Mr Singh", "can": "approve"})
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    s, _ = st()
    assert "3 of 3 issued (Rev A); not sent yet" in s["Drawings issued and sent to the client"]["note"]
    c.post(A + "/step/sent", data={"note": "emailed"})
    s, _ = st()
    assert s["Drawings issued and sent to the client"]["state"] == "done" and s["Client approval"]["state"] == "now"
    # changes asked loops back; approved -> step 9 done
    with db.Session() as d:
        revs = [r.id for r in d.query(db.Revision).order_by(db.Revision.id)]
    c.post("%s/answer-from-client/%d" % (A, revs[1]), data={"outcome": "changes", "name": "Mr Singh", "how": "phone", "note": "1500 long"})
    s, _ = st()
    assert "changes asked on Product 2" in s["Client approval"]["note"]
    for rid in revs:
        c.post("%s/answer-from-client/%d" % (A, rid), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "ok"})
    s, items = st()
    assert s["Client approval"]["state"] == "done" and s["Client approval"]["who"] == "Mr Singh"
    # job order present, delivery date empty in Odoo: blocked with the reason
    j = s["Job order no. and delivery date"]
    assert j["state"] == "blocked" and j["block"] == "Delivery date missing in Odoo"
    assert "Step %d/%d" % (j["no"], len(items)) in c.get("/projects").text
    with db.Session() as d:
        d.query(db.Order).one().delivery_date = "10 DEC 2026"; d.commit()
    c.post("/login/dev/1"); c.post(A + "/step/released", data={"note": ""})
    c.post(A + "/sets/production", data={"job_order": "JO 1"})
    s, _ = st()
    assert s["Production drawings made"]["state"] == "done" and s["Released to production"]["state"] == "done"
    assert s["Bill of material and cut list made"]["state"] == "now"
    # quotation changes appear only when there are some, and block while open
    with db.Session() as d:
        d.add(db.QuoteChange(order_id=d.query(db.Order).one().id, version=2, kind="qty", name="x", stage="production")); d.commit()
    s, _ = st()
    assert s["Quotation changes all handled"]["state"] == "blocked"
    with db.Session() as d:
        d.query(db.QuoteChange).one().status = "handled"; d.commit()
    c.post(A + "/step/completed", data={"note": ""})
    s, items = st()
    assert s["Project marked completed"]["state"] == "done"
    assert cl.summary(items)[0]["title"] == "Bill of material and cut list made"           # the one step still open


def test_old_project_without_history():
    """A project read before this (no answers recorded by name, no access list): a sensible list, no crash."""
    c.post("/login/dev/1")
    c.get("/orders/SAMPLE-REPEAT-1")
    items = cl.steps(appmod.load("SAMPLE-REPEAT-1"))
    assert items[0]["state"] == "done" and cl.summary(items)[0] is not None
    assert "Order checklist" in c.get("/orders/SAMPLE-REPEAT-1").text
    # a salesperson does not see production drawings: those steps are left out, the rest renumbered
    short = cl.steps(appmod.load("SAMPLE-REPEAT-1"), production=False)
    assert not any(x["title"] in cl.PRODUCTION_STEPS for x in short) and [x["no"] for x in short] == list(range(1, len(short) + 1))
    assert len(short) == len(items) - 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
