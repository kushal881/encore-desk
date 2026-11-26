"""The Odoo source against a stand-in Odoo: both interfaces, the order as the desk needs it, and the product photograph."""
import os, sys, io, json, base64, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx, pytest
from PIL import Image
from desk.sources.odoo import OdooSource, OdooError

buf = io.BytesIO(); Image.new("RGB", (30, 20), (10, 120, 60)).save(buf, "PNG")
PNG = base64.b64encode(buf.getvalue()).decode()
DATA = {
    "sale.order": [dict(id=7, name="Q-2026-11-900", partner_id=[5, "Blue Sky Ltd, Ms Rao"], partner_shipping_id=[6, "Blue Sky Ltd, Site"], user_id=[2, "Anchal Dawar"],
                        date_order="2026-11-20 10:00:00", order_line=[71, 72, 73], client_order_ref="", state="sale")],
    "res.partner": [dict(id=5, name="Ms Rao", commercial_company_name="Blue Sky Ltd", parent_id=[4, "Blue Sky Ltd"]),
                    dict(id=6, street="Plot 9, Sector 18", street2="", city="Gurugram", zip="122015", state_id=[12, "Haryana (IN)"])],
    "sale.order.line": [dict(id=71, sequence=10, name="Office Block", product_id=False, product_uom_qty=0, display_type="line_section"),
                        dict(id=73, sequence=30, name="Pedestal 1 Drawer 1 Shutter\nSize : L400 D450 H650mm\nMaterial : 18mm thick prelaminated particle board", product_id=[32, "Pedestal"], product_uom_qty=2, display_type=False),
                        dict(id=72, sequence=20, name="[ESP01] Storage ESP 01 (Size: L1200 D450 H750mm)\nSize: L1200 D450 H750mm\nMaterial: 18mm thick Prelaminated Particle Board", product_id=[31, "Storage ESP 01"], product_uom_qty=4, display_type=False)],
    "product.product": [dict(id=31, image_1024=PNG), dict(id=32, image_1024=False)],
}


def run(model, method, kw):
    rows = DATA[model]
    if method == "search_read":
        dom = kw.get("domain") or []
        return [r for r in rows if all(r.get(f) == v if op == "=" else r.get(f) in v for f, op, v in dom)][: kw.get("limit") or 99]
    if method == "search_count":
        return len(rows)
    if method == "read":
        return [{k: r.get(k) for k in ["id"] + kw["fields"]} for r in rows if r["id"] in kw["ids"]]


def stand_in(json2, key="good"):
    def h(req):
        body = json.loads(req.content or b"{}")
        if req.url.path.startswith("/json/2/"):
            if not json2:
                return httpx.Response(404)
            if req.headers.get("authorization") != "bearer " + key:
                return httpx.Response(401)
            _, _, _, model, method = req.url.path.split("/")
            return httpx.Response(200, json=run(model, method, body))
        p = body["params"]
        if p["service"] == "common":
            return httpx.Response(200, json={"result": 9 if p["args"][2] == key else False})
        db, uid, k, model, method, args, kw = p["args"]
        if args:
            kw = dict(kw, ids=args[0])
        return httpx.Response(200, json={"result": run(model, method, kw)})
    return httpx.Client(transport=httpx.MockTransport(h))


@pytest.mark.parametrize("json2", [True, False])
def test_source(json2):
    o = OdooSource("encore.odoo.com", "encore", "kushal@example.com", "good", client=stand_in(json2))
    assert o.url == "https://encore.odoo.com" and o.configured
    r = o.get_order("Q-2026-11-900")
    assert o.mode == ("json2" if json2 else "rpc")
    assert (r.client_name, r.client_contact, r.salesperson, r.delivery) == ("Blue Sky Ltd", "Ms Rao", "Anchal Dawar", ["Plot 9, Sector 18", "Gurugram 122015, Haryana"])
    assert [(l.no, l.name, l.qty, l.product_ref) for l in r.lines] == [(1, "Storage ESP 01 (Size: L1200 D450 H750mm)", 4, "31"), (2, "Pedestal 1 Drawer 1 Shutter", 2, "32")]
    assert r.lines[0].description.startswith("Size: L1200 D450 H750mm")
    assert o.product_image("31")[:4] == b"\x89PNG" and o.product_image("32") is None
    assert o.list_refs() == ["Q-2026-11-900"] and "Connected. 1 quotations" in o.status()
    with pytest.raises(KeyError):
        o.get_order("NOPE")
    bad = OdooSource("encore.odoo.com", "encore", "kushal@example.com", "expired", client=stand_in(json2))
    with pytest.raises(OdooError):
        bad.get_order("Q-2026-11-900")
    assert bad.status().startswith("Not working")


def test_in_the_desk():
    """An Odoo order opened in the desk: its own records, and the photograph fetched without anyone uploading it."""
    from fastapi.testclient import TestClient
    from desk import app as A, db
    A.ODOO = OdooSource("encore.odoo.com", "encore", "kushal@example.com", "good", client=stand_in(True))
    A.SOURCE = A.Sources(A.ODOO, A.SampleSource())
    c = TestClient(A.app); c.post("/login/dev/1")
    h = c.get("/orders/Q-2026-11-900").text
    assert "Blue Sky Ltd" in h and "/photos/1" in h and h.count("No photo") == 1 and "Product 2." in h
    with db.Session() as s:
        ph = s.query(db.Photo).one()
        assert (ph.product, ph.by) == ("storage esp 01", "Odoo") and os.path.exists(ph.path)
    assert c.get("/orders/Q-2026-10-444").status_code == 200                      # sample orders still open
    d = c.get("/").text
    assert "Odoo:</b> Connected" in d and "Type the quotation or order number" in d
