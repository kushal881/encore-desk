"""Admin 'Delete this test project permanently' (Kushal, 28-Nov-2026): any stage (even issued), typed confirmation,
administrator only, every row of the project and its working files gone; other projects untouched."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from desk.app import app
from desk import db

c = TestClient(app)


def as_(email):
    with db.Session() as s:
        u = s.query(db.User).filter_by(email=email).one().id
    c.post("/logout"); c.post("/login/dev/%d" % u)


def _order(s, ref, client):
    o = db.Order(source="odoo", source_ref=ref, client_id=client.id)
    s.add(o); s.flush()
    l = db.OrderLine(order_id=o.id, no=1, name="Desk", qty=1)
    s.add(l); s.flush()
    s.add(db.Revision(line_id=l.id, number="Encore-Nov-2026-99-01", rev="A", files={}))   # issued: Remove refuses it
    s.add(db.PortalAccess(order_id=o.id, email="x@client.com"))
    s.add(db.Message(order_id=o.id, who="desk", text="hello"))
    return o.id


def test_delete_permanently():
    with db.Session() as s:
        cl = db.Client(name="Test Client"); s.add(cl); s.flush()
        oid, keep = _order(s, "Q-TEST-1", cl), _order(s, "Q-TEST-2", cl)
        s.commit()
    os.makedirs(os.path.join(os.environ["FILES_DIR"], str(oid)), exist_ok=True)
    # not the administrator: refused, nothing changes
    as_("sushil@encoreprimefurniture.com")
    assert c.post("/orders/Q-TEST-1/delete-permanently", data={"confirm": "Q-TEST-1"}, follow_redirects=False).status_code == 403
    as_("kushal@encoreprimefurniture.com")
    assert "Delete this test project permanently" in c.get("/orders/Q-TEST-1").text
    # wrong or empty confirmation: refused, nothing changes
    for bad in ("", "Q-TEST", "q-test-1", "Q-TEST-2"):
        assert c.post("/orders/Q-TEST-1/delete-permanently", data={"confirm": bad}, follow_redirects=False).status_code == 400
    with db.Session() as s:
        assert s.get(db.Order, oid) is not None
    # right confirmation: the issued project and everything it owns are gone
    assert c.post("/orders/Q-TEST-1/delete-permanently", data={"confirm": "Q-TEST-1"}, follow_redirects=False).status_code == 303
    with db.Session() as s:
        assert s.get(db.Order, oid) is None
        assert s.scalar(select(func.count()).select_from(db.Revision)) == 1          # only Q-TEST-2's
        assert s.scalar(select(func.count()).select_from(db.PortalAccess)) == 1
        assert s.get(db.Order, keep) is not None
    assert not os.path.exists(os.path.join(os.environ["FILES_DIR"], str(oid)))
