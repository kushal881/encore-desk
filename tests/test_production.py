"""Screen 11: Production. Deadline from Odoo's delivery date (decision 3), the production sheet with its tabs, copy for a
department, release P1, and construction-only changes: refused when the client drawing would change (checked by drawing it
and comparing with the approved revision), accepted into P1 before release, and P1 -> P2 by an administrator after
release with a stop alert. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, assistant, production as pr
from desk.sources import odoo

c = TestClient(app)
A = "/orders/Q-2026-10-444"
P = A + "/production/3"                                                # the pedestal
KUSHAL, SUSHIL, ANCHAL = 1, 2, 3


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def test_odoo_day():
    assert odoo._day("2026-12-10 05:30:00") == "10 DEC 2026" and odoo._day(False) == "" and odoo._day("") == "" and odoo._day("not a date") == ""


def set3():
    with db.Session() as s:
        l = s.query(db.OrderLine).filter_by(no=3).one()
        return s.query(db.ProductionSet).filter_by(line_id=l.id, kind="PRODUCTION").order_by(db.ProductionSet.id.desc()).first()


def test_production(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(SUSHIL)
    c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    assert c.get(P).status_code == 200 and "Make the production drawings first" in c.get(P).text        # not released yet
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Step(order_id=o.id, kind="approved", note="Mr Singh, by email", by="Sushil Singhal")); s.commit()   # old data: approved as a step
    as_(KUSHAL); c.post(A + "/step/released", data={"note": ""}); as_(SUSHIL)
    # decision 3: no delivery date in Odoo and none typed -> blocked with the plain message
    r = c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0119"})
    assert r.status_code == 400 and "Ask sales to enter the delivery date in Odoo" in r.text
    assert "Ask sales to enter the delivery date in Odoo" in c.get(P).text
    with db.Session() as s:                                            # the delivery date read from Odoo is used
        s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().delivery_date = "10 DEC 2026"; s.commit()
    c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0119"})
    ps = set3()
    assert ps is not None and ps.prod_no == 1 and ps.details["deadline"] == "10 DEC 2026" and ps.spec and not ps.released_at
    h = c.get(P).text
    assert "production drawing P1" in h and "Client design locked" in h and "P1 not released yet" in h and "deadline 10 DEC 2026" in h
    assert "Open production sheet" in c.get(A).text
    # copy for a department: stamped; vendor needs the subcontractor drawings
    for dept in ("all", "panel", "metal", "assembly"):
        r = c.get(P + "/copy?dept=" + dept)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    import pymupdf
    assert "COPY FOR: MS FABRICATION" in pymupdf.open(stream=c.get(P + "/copy?dept=metal").content, filetype="pdf")[0].get_text()
    assert c.get(P + "/copy?dept=vendor").status_code == 400 and c.get(P + "/copy?dept=nope").status_code == 404
    # BOM / cut list tabs: before and after the bill of material is made
    assert "has not been made for this order yet" in c.get(P + "?tab=bom").text
    c.post(A + "/bom")
    h = c.get(P + "?tab=cut").text
    assert "From " in h and "<th>Part</th>" in h
    assert "<th>Item</th>" in c.get(P + "?tab=bom").text

    # empty text: refused
    assert "Type the change first" in c.post(P + "/ai", data={"text": "  "}).text
    # a design change without the AI key (the desk's own rules): refused, the RevB route is offered
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    h = c.post(P + "/ai", data={"text": "make it 600 deep"}).text
    assert "changes the approved design" in h and "Ask admin permission" in h
    # a model change the engine shows on the client drawing too: refused after drawing and comparing
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(assistant, "_call", lambda system, user: dict(kind="change", said="Drawer channel 400 -> 350", answer="",
                                                                       changes=[dict(path="model.slide_len", value="350")]))
    h = c.post(P + "/ai", data={"text": "use 350 channels", "marker": "0"}).text
    assert "the client drawing would not be the one the client approved" in h
    # a construction change that leaves the client drawing as approved (the comparison is stood in for here, the engine
    # has no production-only value yet): offered with its BOM effect, then accepted into P1 before release
    monkeypatch.setattr(pr, "client_unchanged", lambda r, model, key, edir: (True, [dict(title="x", ok=True)], ""))
    c.post(P + "/ai", data={"text": "use 350 channels"})
    with db.Session() as s:
        pc = s.query(db.ProdChange).filter_by(status="trial").one()
    h = c.get(P).text
    assert "Proposed change · production only" in h and "Client drawing:</b> no change" in h and "BOM:" in h
    assert "Accept or undo the waiting change before release" in h
    assert "already waiting" in c.post(P + "/ai", data={"text": "another"}).text                  # one at a time
    assert c.post(P + "/release").status_code == 400                                               # a change is waiting
    c.post(P + "/ai/%d/accept" % pc.id)
    ps = set3()
    assert ps.prod_no == 1 and ps.spec["model"]["slide_len"] == 350 and not ps.released_at
    # release P1
    c.post(P + "/release")
    ps = set3()
    assert ps.released_by == "Sushil Singhal" and "P1 released by Sushil Singhal" in c.get(P).text
    assert c.post(P + "/release").status_code == 400
    # made again (new job order): keeps P1, its release and the accepted change
    c.post(A + "/sets/production", data={"job_order": "JO 26-27-11-0120"})
    ps = set3()
    assert ps.prod_no == 1 and ps.released_at and ps.spec["model"]["slide_len"] == 350 and ps.details["job_order"] == "JO 26-27-11-0120"
    # after release: the change needs an administrator, P1 -> P2 with a stop alert
    monkeypatch.setattr(assistant, "_call", lambda system, user: dict(kind="change", said="Drawer channel 350 -> 300", answer="",
                                                                       changes=[dict(path="model.slide_len", value="300")]))
    c.post(P + "/ai", data={"text": "use 300 channels"})
    with db.Session() as s:
        pc = s.query(db.ProdChange).filter_by(status="trial").one()
    assert "Ask admin for P2" in c.get(P).text
    c.post(P + "/ai/%d/accept" % pc.id)
    with db.Session() as s:
        cr = s.query(db.ChangeRequest).filter_by(kind="production").one()
        assert cr.text.startswith("P1 -> P2") and cr.status == "asked"
    assert "Waiting for an administrator (P1" in c.get(P).text
    as_(ANCHAL)
    assert c.post("/approvals/production/%d/approve" % cr.id).status_code == 403
    as_(SUSHIL)                                                          # a secondary administrator may allow production changes
    assert "Production changes" in c.get("/approvals?tab=production").text
    c.post("/approvals/production/%d/approve" % cr.id)
    ps = set3()
    assert ps.prod_no == 2 and ps.spec["model"]["slide_len"] == 300 and not ps.released_at
    with db.Session() as s:
        alerts = s.query(db.Task).filter(db.Task.source.like("prodstop:%")).all()
        assert {"Kushal Singhal", "Sushil Singhal"} <= {t.assignee for t in alerts} and all(t.text.startswith("STOP: production drawing of Product 3") for t in alerts)
        assert s.query(db.ProdChange).filter_by(id=pc.id).one().status == "approved"
    assert "production drawing P2" in c.get(P).text
    # old data: a production set made before sets kept their specification is drawn from the revision
    with db.Session() as s:
        s.query(db.ProductionSet).filter_by(id=ps.id).one().spec = {}; s.commit()
    assert c.get(P).status_code == 200
    with db.Session() as s:
        r = s.query(db.Revision).join(db.OrderLine).filter(db.OrderLine.no == 3).one()
    assert pr.base_spec(set3(), r)["model"] == r.spec["model"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
