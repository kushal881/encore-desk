"""Products tab and product switcher (issue #84): one list for 100+ products, search, filter chips with counts, area
groups, tick boxes and one bulk Issue; the drawing page switcher "Product 2 of …" with search.
Covers old data (lines without areas or drawings), an AI change still waiting, an approved / locked product (shown,
never ticked), an empty area ("No area"), bought-in lines ("No drawing"), and 100 products. Viewer parts need ENGINES_DIR."""
import os, sys, tempfile, re
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app, load
from desk import db, engines
from desk.products import rows_for, groups_of, counts, NO_AREA

A = "/orders/Q-2026-10-444"
c = TestClient(app)
c.post("/login/dev/1")


def row(html, no):
    m = re.search(r'class="prow" role="row"[^>]*data-text="%d .*?</div>' % no, html, re.S)
    return m.group(0) if m else ""


def test_old_order_and_no_drawing_lines():
    """Old data: nothing drawn yet, no areas -> every product listed under "No area", "Not drawn yet"; set-aside lines say No drawing."""
    c.get(A)
    o = load("Q-2026-10-444")
    rows = rows_for(o)
    drawn = [r for r in rows if r["key"] != "nodrawing"]
    assert [r["no"] for r in drawn] == sorted(l.no for l in o.lines)
    assert all(r["area"] == NO_AREA for r in drawn)
    assert groups_of(rows)[-1][0] == NO_AREA
    h = c.get(A + "/products").text
    assert "Product 1 · " in h and "No area · " in h and "Drawing 1" not in h
    assert 'href="%s/products"' % A in h and "Products · %d" % (len(o.lines) + len(o.not_drawn)) in h
    with db.Session() as s:
        od = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.NoDrawingLine(order_id=od.id, no=99, name="Installation and transport", qty=1, reason="Service", by=""))
        s.commit()
    o = load("Q-2026-10-444")
    h = c.get(A + "/products").text
    nd = [r for r in rows_for(o) if r["key"] == "nodrawing"]
    assert nd and not any(r["pick"] for r in nd) and "No drawing · service" in h and "Installation and transport" in h


def test_statuses_ai_waiting_and_approved():
    """AI change waiting shows as such; an approved product is shown but cannot be ticked or issued."""
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        ls = {l.no: l for l in s.query(db.OrderLine).filter_by(order_id=o.id)}
        for l in ls.values():
            if l.drawing is None:
                l.drawing = db.Drawing(status="preview", engine_version="v11", spec={}, files={})
        l1 = ls[1]
        r = db.Revision(line_id=l1.id, number="Encore-Nov-2026-09-01", rev="A", engine_version="v11", spec={"model": None}, files={})
        s.add(r); s.flush()
        l1.drawing.spec, l1.drawing.made_at = {"model": None}, r.issued_at
        s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="approved", name="Mr Singh", how="email"))
        s.commit()
    o = load("Q-2026-10-444")
    o.lines[1].ai_waiting = True if len(o.lines) > 1 else None
    by = {r["no"]: r for r in rows_for(o)}
    assert by[1]["key"] == "approved" and not by[1]["pick"] and not by[1]["issuable"]
    if len(o.lines) > 1:
        assert by[o.lines[1].no]["text"] == "AI change waiting" and by[o.lines[1].no]["pick"]
    h = c.get(A + "/products").text
    assert "Approved" in row(h, 1) and 'class="pick"' not in row(h, 1)
    # bulk issue never issues the approved product, even when its number is sent
    before = 0
    with db.Session() as s:
        before = s.query(db.Revision).count()
    c.post(A + "/issue", data={"folder_no": "6", "nos": "1", "back": "issue"})
    with db.Session() as s:
        assert s.query(db.Revision).count() == before


def test_hundred_products_search_and_select():
    """100 products load in one page with areas grouped; counts add up; every row has a search text."""
    c.get("/orders/SAMPLE-REPEAT-1")
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        top = max([l.no for l in s.query(db.OrderLine).filter_by(order_id=o.id)] or [0])
        for no in range(top + 1, top + 101):
            s.add(db.OrderLine(order_id=o.id, no=no, name="Workstation %d (1200 x 600)" % no, qty=2))
        s.commit()
    c.get("/orders/SAMPLE-REPEAT-1")
    o = load("SAMPLE-REPEAT-1")
    rows = rows_for(o)
    assert len([r for r in rows if r["key"] != "nodrawing"]) >= 100
    cn = counts(rows)
    assert cn["all"] == len(rows) and cn["review"] + cn["ready"] <= cn["all"]
    h = c.get("/orders/SAMPLE-REPEAT-1/products").text
    assert h.count('class="prow" role="row"') == len(rows)
    assert 'data-text="%d workstation %d' % (top + 100, top + 100) in h and "Search %d products" % len(rows) in h
    assert 'id="pissue" disabled' in h and "Issue 0 to client" in h          # nothing ticked: nothing to issue


def test_switcher_on_drawing_page():
    """The drawing page header opens a list of every drawn product with search and status filters."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post(A + "/draw")
    o = load("Q-2026-10-444")
    no = next(l.no for l in o.lines if l.drawing is not None and l.drawing.status != "failed")
    v = c.get("%s/view/%d" % (A, no)).text
    assert 'id="psw"' in v and 'id="swq"' in v and "Find a product" in v
    assert v.count('role="option"') == len([l for l in o.lines if l.drawing or l.revisions])
    assert 'data-swf="ai"' in v and 'data-swf="ready"' in v


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
