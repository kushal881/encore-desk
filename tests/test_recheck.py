"""Orders read before the no-drawing rule existed get it when the project is opened (or "Check Odoo again"): bought-out
items (Odoo route Buy), chairs, services, installation and delivery move to "No drawing needed" with their numbers kept.
Not moved: a product with an issued drawing, or one added back to the drawings on purpose."""
import os, sys, tempfile, copy
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk import app as appmod, db
from desk.app import app
from desk.nodrawing import classify
from desk.sources.base import SourceLine
from desk.sources.odoo import OdooError

A = "/orders/Q-2026-10-444"
c = TestClient(app)
OLD = [(4, "Evolve Chair (Mesh, black)"), (5, "Legacy"), (6, "Flip Top Box"), (7, "Installation and Delivery"),
       (8, "Installation charges"), (9, "Visitor chair")]


def test_chairs_are_bought_out():
    assert classify("Evolve Chair (Mesh, black)") == "Bought-out item" and classify("Visitor chairs") == "Bought-out item"
    assert classify("Chairman table L2400") == "" and classify("Workstation EDS 02") == ""


def odoo_now(real):
    """Odoo as it stands now: the old lines are in the quotation; Legacy and the Flip Top Box are bought out (route Buy)."""
    def get_order(ref):
        o = copy.deepcopy(real(ref))
        for no, name in OLD:
            why = {"Legacy": "Bought-out item", "Flip Top Box": "Bought-out item"}.get(name) or classify(name)
            o.lines.append(SourceLine(no=0, name=name, description="", qty=1, product_ref="", no_drawing=why))
        return o
    return get_order


def _lines():
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        return {l.no: l.name for l in o.lines}, {x.no: (x.name, x.reason) for x in o.not_drawn}


def test_old_order_gets_the_rule(monkeypatch):
    c.post("/login/dev/2")
    c.get(A)
    with db.Session() as s:                                   # the order as an earlier version read it: every line a product
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        for no, name in OLD:
            l = db.OrderLine(order_id=o.id, no=no, name=name, description="", qty=1)
            l.questions.append(db.Question(key="size", text="Size?", options=[]))
            s.add(l)
        s.flush()
        by = {l.no: l for l in o.lines}
        by[7].drawing = db.Drawing(status="failed", error="No approved template")
        s.add(db.Revision(line_id=by[8].id, number="Encore-Nov-2026-06-08", rev="A"))
        by[9].keep = True                                     # added back to the drawings on purpose
        s.commit()

    monkeypatch.setattr(appmod.SOURCE, "get_order", odoo_now(appmod.SOURCE.get_order))
    h = c.get(A).text
    drawn, aside = _lines()
    assert sorted(drawn) == [1, 2, 3, 8, 9]
    assert aside == {4: ("Evolve Chair (Mesh, black)", "Bought-out item"), 5: ("Legacy", "Bought-out item"),
                     6: ("Flip Top Box", "Bought-out item"), 7: ("Installation and Delivery", "Installation, delivery or transport")}
    assert "No drawing needed (4)" in h and "was Product 4" in h
    assert "Product 8 (Installation charges) needs no drawing" in h and "quotation change" in h
    with db.Session() as s:
        alive = {l.id for l in s.query(db.OrderLine)}                         # questions went with the products moved
        assert all(q.line_id in alive for q in s.query(db.Question))
        assert s.query(db.Drawing).filter_by(status="failed").count() == 0
    w = c.get(A + "/work").text
    assert "No drawing needed for Product 4 (Evolve Chair, bought-out item); Product 5 (Legacy, bought-out item)" in w

    assert c.get(A).status_code == 200 and _lines()[0].keys() == {1, 2, 3, 8, 9}   # opening again changes nothing
    r = c.post(A + "/lines/add", data={"name": "Extra side table", "qty": "1", "details": ""}, follow_redirects=False)
    assert 10 in _lines()[0]                                                       # numbers are never reused

    # a chair added back on purpose stays on the drawings
    with db.Session() as s:
        nid = s.query(db.NoDrawingLine).filter_by(no=4).one().id
    c.post("%s/not-drawn/%d/add" % (A, nid))
    c.get(A)
    assert 4 in _lines()[0]


def test_odoo_down_name_rules_still_apply(monkeypatch):
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.OrderLine(order_id=o.id, no=11, name="Delivery to site", description="", qty=1))
        s.add(db.OrderLine(order_id=o.id, no=12, name="Cable tray", description="", qty=1))
        s.commit()
    def down(ref):
        raise OdooError("Odoo is not reachable")
    monkeypatch.setattr(appmod.SOURCE, "get_order", down)
    r = c.post(A + "/recheck", follow_redirects=False)
    assert r.headers["location"] == A
    drawn, aside = _lines()
    assert 11 in aside and 12 in drawn


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
