"""Lines that get no drawing: bought-out (Odoo route Buy), services, installation/delivery words. Needs ENGINES_DIR for the desk part."""
import os, sys, json, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from desk.nodrawing import classify
from desk.sources.odoo import OdooSource


def test_rule():
    assert classify("Evolve chair EEC 589", "consu", ["Buy"]) == "Bought-out item"
    assert classify("Storage ESP 01", "consu", ["Buy", "Manufacture"]) == ""          # made here as well: drawn
    assert classify("Storage ESP 01", "consu", ["Manufacture"]) == ""
    assert classify("Site work", "service") == "Service"
    for name in ("Installation charges", "Transportation", "Delivery to site", "Freight", "Cartage and unloading", "Shipping"):
        assert classify(name) == "Installation, delivery or transport", name
    assert classify("ZEE Workstation (Single Seat)") == ""
    assert classify("Pedestal 2 drawer") == ""


DATA = {
    "sale.order": [dict(id=7, name="Q-2026-11-950", partner_id=[5, "Blue Sky Ltd"], partner_shipping_id=False, user_id=False,
                        date_order="2026-11-27 10:00:00", order_line=[1, 2, 3, 4, 5], client_order_ref="")],
    "res.partner": [dict(id=5, name="Blue Sky Ltd", commercial_company_name="Blue Sky Ltd", parent_id=False)],
    "sale.order.line": [
        dict(id=1, sequence=1, name="Storage ESP 01\nSize: L1200 D450 H750mm", product_id=[31, "Storage"], product_uom_qty=2, display_type=False),
        dict(id=2, sequence=2, name="Evolve chair EEC 589", product_id=[40, "Chair"], product_uom_qty=10, display_type=False),
        dict(id=3, sequence=3, name="Installation charges", product_id=[41, "Installation"], product_uom_qty=1, display_type=False),
        dict(id=4, sequence=4, name="Pedestal 1 Drawer 1 Shutter\nSize : L400 D450 H650mm", product_id=[32, "Pedestal"], product_uom_qty=2, display_type=False),
        dict(id=5, sequence=5, name="Cable tray", product_id=[42, "Cable tray"], product_uom_qty=5, display_type=False)],
    "product.product": [dict(id=31, type="consu", route_ids=[1]), dict(id=32, type="consu", route_ids=[1]), dict(id=40, type="consu", route_ids=[2]),
                        dict(id=41, type="service", route_ids=[]), dict(id=42, type="consu", route_ids=[2])],
    "stock.route": [dict(id=1, name="Manufacture"), dict(id=2, name="Buy")],
}


def fake_odoo():
    def h(req):
        _, _, _, model, method = req.url.path.split("/")
        kw = json.loads(req.content or b"{}")
        rows = DATA[model]
        if method == "search_read":
            return httpx.Response(200, json=[r for r in rows if r["name"] == kw["domain"][0][2]])
        return httpx.Response(200, json=[{k: r.get(k) for k in ["id"] + kw["fields"]} for r in rows if r["id"] in kw["ids"]])
    return OdooSource(url="https://odoo.test", db="d", user="u", key="k", client=httpx.Client(transport=httpx.MockTransport(h)))


def test_odoo_lines():
    o = fake_odoo().get_order("Q-2026-11-950")
    drawn = [(l.no, l.name.split("\n")[0]) for l in o.lines if not l.no_drawing]
    assert drawn == [(1, "Storage ESP 01"), (2, "Pedestal 1 Drawer 1 Shutter")]            # numbered Product 1, 2 without gaps
    assert {l.name: l.no_drawing for l in o.lines if l.no_drawing} == {
        "Evolve chair EEC 589": "Bought-out item", "Installation charges": "Service", "Cable tray": "Bought-out item"}


def test_desk_keeps_them_apart():
    from desk.sources import sample
    d = tempfile.mkdtemp()
    json.dump(dict(source="sample", ref="SAMPLE-ND-1", client=dict(name="Test Client"), lines=[
        dict(no=1, qty=4, name="Pedestal 2 drawer", description="Size : L400 D450 H650mm"),
        dict(no=2, qty=4, name="Evolve chair EEC 589", description="", routes=["Buy"]),
        dict(no=3, qty=1, name="Transportation and delivery", description="")]), open(os.path.join(d, "SAMPLE-ND-1.json"), "w"))
    sample.HERE = d
    from fastapi.testclient import TestClient
    from desk.app import app
    from desk import db
    c = TestClient(app); c.post("/login/dev/1")
    h = c.get("/orders/SAMPLE-ND-1").text
    assert "No drawing needed (2)" in h and "Evolve chair EEC 589" in h and "Bought-out item" in h and "Installation, delivery or transport" in h
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-ND-1").one()
        assert [(l.no, l.name) for l in o.lines] == [(1, "Pedestal 2 drawer")]                # only the pedestal gets questions and a drawing
        assert sorted(x.reason for x in o.not_drawn) == ["Bought-out item", "Installation, delivery or transport"]
        assert "1 product to draw in this order (2 more lines need no drawing)" in o.messages[0].text


def test_remove_and_add_lines():
    """Remove a product, add it back (same number), add a set-aside line, add a product not in the quotation. Runs after the test above."""
    from fastapi.testclient import TestClient
    from desk.app import app
    from desk import db
    c = TestClient(app); c.post("/logout"); c.post("/login/dev/2")                      # any signed-in user, not only the administrator
    B = "/orders/SAMPLE-ND-1"
    c.get(B)
    assert c.post(B + "/lines/9/remove", data=dict(reason="Duplicate line")).status_code == 404
    r = c.post(B + "/lines/1/remove", data=dict(reason="Not needed by client"), follow_redirects=False)
    assert r.status_code == 303
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-ND-1").one()
        assert o.lines == []
        gone = next(x for x in o.not_drawn if x.no == 1)
        assert gone.reason == "Removed: Not needed by client" and gone.by == "Sushil Singhal" and "L400" in gone.description
        chair = next(x for x in o.not_drawn if x.reason == "Bought-out item")
        gone_id, chair_id = gone.id, chair.id
    h = c.get(B).text
    assert "was Product 1" in h and "Removed: Not needed by client" in h
    c.post(B + "/not-drawn/%d/add" % gone_id)                                            # back as Product 1
    c.post(B + "/not-drawn/%d/add" % chair_id)                                           # the chair, on purpose: next number
    c.post(B + "/lines/add", data=dict(name="Pedestal 3 drawer", qty="2", details="Size : L400 D450 H650mm"))
    assert c.post(B + "/lines/add", data=dict(name="", qty="1")).status_code == 400
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-ND-1").one()
        assert [(l.no, l.name) for l in o.lines] == [(1, "Pedestal 2 drawer"), (2, "Evolve chair EEC 589"), (3, "Pedestal 3 drawer")]
        assert [(a.no, a.by) for a in o.added] == [(3, "Sushil Singhal")]
        assert [x.reason for x in o.not_drawn] == ["Installation, delivery or transport"]
        l1 = o.lines[0]
        s.add(db.Revision(line_id=l1.id, number="TEST-01", rev="A")); s.commit()             # pretend product 1 was issued
    assert c.post(B + "/lines/1/remove", data=dict(reason="Other")).status_code == 400   # issued: a quotation change, not a removal
    h = c.get(B).text
    assert "Quotation needs update" in h and "Product 3 · Pedestal 3 drawer" in h
