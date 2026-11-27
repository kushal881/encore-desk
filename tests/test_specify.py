"""Specify (screen 4): one product at a time, areas per product, what is still missing, what will be built.
The drawing part needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, specify

A = "/orders/Q-2026-10-444"
B = "/orders/SAMPLE-REPEAT-1"


def test_area_rules():
    assert specify.check_areas([("", ""), ("", "")], 3) == ([], "")                       # blank: allowed
    assert specify.check_areas([("Office Block", "2"), ("Cabin", "1")], 3) == ([("Office Block", 2), ("Cabin", 1)], "")
    assert "add up to 2" in specify.check_areas([("Office Block", "2")], 3)[1]
    assert "needs an area name" in specify.check_areas([("", "2")], 2)[1]
    assert "whole number" in specify.check_areas([("Cabin", "1.5")], 1.5)[1] and "not a number" in specify.check_areas([("Cabin", "x")], 1)[1]
    assert specify.area_text([("Office Block", 3)], 3) == "Office Block"
    assert specify.area_text([("Office Block", 2), ("Cabin", 1)], 3) == "Office Block (2), Cabin (1)" and specify.area_text([], 3) == ""


def test_status_and_summary():
    assert specify.status(NS(open_count=2, drawing=None)) == "todo"
    assert specify.status(NS(open_count=0, drawing=NS(status="failed"))) == "todo"
    assert specify.status(NS(open_count=0, drawing=None)) == "done"
    s = dict(specify.summary({"item": {"product_code": "1200 x 450 x 750", "qty": 3, "area": "Cabin"},
                              "model": {"shelves": 2, "hinge": "left", "screen": {"w": 900}, "top_rgb": [1, 2, 3]},
                              "swatches": [{"code": "LAUREL OAK DARK 3416", "use": "Carcass, top"}]}))
    assert s == {"Size": "1200 x 450 x 750", "Quantity": 3, "Area": "Cabin", "Shelves": 2, "Hinge side": "left",
                 "Screen width": 900, "Finish: Carcass": "LAUREL OAK DARK 3416"}


c = TestClient(app)


def _qid(key):
    with db.Session() as s:
        return s.query(db.Question).filter_by(key=key, answer="").first().id


def test_specify_page():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2")
    c.get(A)
    r = c.get(A + "/specify", follow_redirects=False)
    assert r.headers["location"] == A + "/specify/1"                                      # the first product that needs something
    v = c.get(A + "/specify/2").text
    assert "Step 2 of 8 · Specify" in v and "Product 2 of 3" in v and "0 of 3 specified" in v
    assert "Engine template: <b>cabinet_shutters_partition_shelves</b>" in v and "approved" in v
    assert "How many shelves inside?" in v and 'name="back" value="specify"' in v and "Summary to build" not in v
    assert 'href="/orders/Q-2026-10-444/specify">Specify</a>' in v                     # the tab

    # answering on Specify comes back to Specify; then the summary appears and the product counts as done
    r = c.post("%s/answer/%d" % (A, _qid("size")), data={"value": "L1200 D450 H750", "back": "specify"}, follow_redirects=False)
    assert r.headers["location"] == A + "/specify/2"
    c.post("%s/answer/%d" % (A, _qid("shelves")), data={"value": "2", "back": "specify"})
    v = c.get(A + "/specify/2").text
    assert "Nothing is missing." in v and "Summary to build" in v and "<div>Shelves</div><div>2</div>" in v and "Draw the preview" in v
    assert "1 of 3 specified" in v

    # areas: must add up to the quantity; they go on the sheet
    with db.Session() as s:
        qty = s.query(db.OrderLine).filter_by(no=2).one().qty
    r = c.post(A + "/specify/2/areas", data={"area": ["Office Block", "Cabin"], "qty": ["1", "5"]}, follow_redirects=True)
    assert "must be equal" in r.text
    with db.Session() as s:
        assert s.query(db.LineArea).count() == 0
    c.post(A + "/specify/2/areas", data={"area": ["Office Block", ""], "qty": ["%d" % qty, ""]})
    v = c.get(A + "/specify/2").text
    assert 'value="Office Block"' in v and "<div>Area</div><div>Office Block</div>" in v
    if qty >= 2:
        c.post(A + "/specify/2/areas", data={"area": ["Office Block", "Cabin"], "qty": ["%d" % (qty - 1), "1"]})
        assert "Office Block (%d), Cabin (1)" % (qty - 1) in c.get(A + "/specify/2").text
    c.post(A + "/draw", data={"no": "2"})
    with db.Session() as s:
        d = s.query(db.OrderLine).filter_by(no=2).one().drawing
        assert d.status == "preview" and d.spec["item"]["area"].startswith("Office Block")
    assert "Open drawing in viewer" in c.get(A + "/specify/2").text
    c.post(A + "/specify/2/areas", data={"area": [""], "qty": [""]})                     # cleared: blank again
    with db.Session() as s:
        assert s.query(db.LineArea).count() == 0


def test_engine_cannot_build():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.get(B)
    with db.Session() as s:
        no = next(l.no for l in s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "SAMPLE-REPEAT-1") if "Conference" in l.name)
        for q in s.query(db.Question).join(db.OrderLine).filter(db.OrderLine.no == no):
            q.answer = q.answer or (q.options[0] if q.options else "L2400 D1200 H750")
        s.commit()
    v = c.get("%s/specify/%d" % (B, no)).text
    assert "The engine can't build this yet" in v and "Conference tables are not ready to draw yet" in v and "Request engine change" in v
    r = c.post("/engine-requests", data={"text": "Conference table template", "order_ref": "SAMPLE-REPEAT-1", "product": "Product %d" % no})
    with db.Session() as s:
        assert s.query(db.EngineRequest).one().product == "Product %d" % no


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
