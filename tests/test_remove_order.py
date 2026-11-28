"""Issue #50: 'Remove from the desk' must remove an order with rows in every newer table (quotation versions and changes,
portal access, email log, AI turns, reviews, production previews ...) without an error, and leave nothing behind.
'Remove from drawings' for one product likewise. A guard test fails when a new table refers to an order / line / revision
without a database link that removal follows."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import event, Integer
from fastapi.testclient import TestClient
from desk.app import app
from desk import db

# SQLite only checks foreign keys when asked to; Postgres (live) always does. Ask, so the test fails as live did.
@event.listens_for(db.engine, "connect")
def _fk_on(con, _):
    con.execute("PRAGMA foreign_keys=ON")
db.engine.dispose()

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def _fill(order_id):
    """One row in every table that belongs to an order or its products, as an order accumulates them."""
    with db.Session() as s:
        o = s.get(db.Order, order_id)
        l = o.lines[0]
        s.add_all([db.PortalAccess(order_id=o.id, email="a@b.in"), db.EmailLog(order_id=o.id, to="a@b.in"),
                   db.ProductionFile(order_id=o.id), db.ClientAction(order_id=o.id, approval_id=0, kind="reply"),
                   db.AiTurn(line_id=l.id, typed="x", kind="change", status="trial"), db.Review(line_id=l.id, shown="d1"),
                   db.ProdPreview(line_id=l.id, shown="d1"), db.Mark(line_id=l.id, shown="d1", no=1, x=1, y=1),
                   db.LineArea(line_id=l.id, area="Reception"), db.Change(line_id=l.id, typed="x"),
                   db.FileLog(order_id=o.id, name="kept as history"), db.PortalCode(email="a@b.in", order_id=o.id, code_hash="x" * 64, expires=db.now())])
        for name in ("QuoteVersion", "QuoteChange", "ProdChange"):
            M = getattr(db, name, None)
            if M is None:
                continue
            row = M()
            for col in M.__table__.columns:
                if col.primary_key or col.nullable or col.default is not None:
                    continue
                v = o.id if col.name == "order_id" else l.id if col.name == "line_id" else 0 if isinstance(col.type, Integer) else ""
                setattr(row, col.key, v)
            if "order_id" in M.__table__.c:
                row.order_id = o.id
            if "line_id" in M.__table__.c:
                row.line_id = l.id
            s.add(row)
        s.commit()
        return l.id


def _left(order_id, line_ids):
    """Rows still pointing at the order or its lines, per table (logs kept on purpose are not counted)."""
    out = {}
    with db.Session() as s:
        for t in db.Base.metadata.sorted_tables:
            for col, ids in (("order_id", [order_id]), ("line_id", line_ids)):
                if col in t.c and (t.name, col) not in db.UNLINKED_KEPT:
                    n = s.execute(t.select().where(t.c[col].in_(ids))).fetchall()
                    if n:
                        out[t.name] = len(n)
    return out


def test_every_reference_is_followed():
    """Guard: a table that refers to an order, a product or a revision must do it with a database link (followed by removal),
    or be listed in db.UNLINKED_KEPT as kept on purpose. A new table that does neither fails here."""
    linked = {(t.name, fk.parent.name) for t in db.Base.metadata.sorted_tables for fk in t.foreign_keys}
    bad = [(t.name, col.name) for t in db.Base.metadata.sorted_tables for col in t.columns
           if col.name in ("order_id", "line_id", "revision_id", "approval_id") and (t.name, col.name) not in linked and (t.name, col.name) not in db.UNLINKED_KEPT]
    assert bad == []


def test_remove_order_with_rows_everywhere():
    c.post("/login/dev/1"); c.get(A)
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        oid, lids = o.id, [l.id for l in o.lines]
    _fill(oid)
    assert _left(oid, lids)                                                   # the order has rows in many tables
    c.post("/logout"); c.post("/login/dev/2")
    assert c.post(A + "/remove").status_code == 403                           # administrator only
    c.post("/logout"); c.post("/login/dev/1")
    r = c.post(A + "/remove", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert _left(oid, lids) == {}                                             # nothing left behind
    with db.Session() as s:
        assert s.get(db.Order, oid) is None
        assert s.query(db.FileLog).filter_by(order_id=oid).count() == 1       # the view log is history: kept


def test_issued_order_still_refused_and_product_removal():
    c.post("/login/dev/1"); c.get(A)                                          # read again from the sample source
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        oid = o.id
        l = o.lines[1]
        lid, no = l.id, l.no
    _fill(oid)
    # 'Remove from drawings' on a product with AI turns, reviews, marks ...: no error, moves to No drawing needed
    with db.Session() as s:
        for M in (db.AiTurn, db.Review, db.Mark):
            s.add(M(line_id=lid, **({"typed": "x", "kind": "change", "status": "trial"} if M is db.AiTurn else {"shown": "d1"} if M is db.Review
                                     else {"shown": "d1", "no": 1, "x": 1, "y": 1})))
        s.commit()
    r = c.post("%s/lines/%d/remove" % (A, no), data={"reason": "Not needed by client"}, follow_redirects=False)
    assert r.status_code == 303
    assert _left(0, [lid]) == {}
    with db.Session() as s:
        nd = s.query(db.NoDrawingLine).filter_by(order_id=oid, no=no).one()
        nid = nd.id
    assert c.post("%s/not-drawn/%d/add" % (A, nid), follow_redirects=False).status_code == 303      # added back: no error
    # an order with an issued revision (old data or approved) is still refused, with the message
    with db.Session() as s:
        l0 = s.query(db.OrderLine).filter_by(order_id=oid).order_by(db.OrderLine.no).first()
        s.add(db.Revision(line_id=l0.id, number="Encore-Nov-2026-06-01", rev="A", files={})); s.commit()
    r = c.post(A + "/remove")
    assert r.status_code == 400 and "issued drawings and cannot be removed" in r.text
    with db.Session() as s:
        assert s.get(db.Order, oid) is not None


def test_empty_order_removed():
    """An order with nothing but its products (empty values everywhere) is removed too."""
    with db.Session() as s:
        o = db.Order(client=db.Client(name="Empty Client"), source="sample", source_ref="EMPTY-1")
        o.lines.append(db.OrderLine(no=1, name="Desk", qty=1))
        s.add(o); s.commit(); oid = o.id
        assert db.delete_order(s, oid) >= 2
        s.commit()
    with db.Session() as s:
        assert s.get(db.Order, oid) is None and s.query(db.OrderLine).filter_by(order_id=oid).count() == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
