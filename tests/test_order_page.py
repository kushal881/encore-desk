"""Order page fixes (issue #69): one "Drawings" menu instead of a tab per product (Product N, never Drawing N), search
boxes that filter as you type and show everything when cleared, and the checklist chip in the same action words as the
"What to do next" bar. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile, re
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app, load, guide
from desk import db, engines
from desk.checklist import steps, summary

A = "/orders/Q-2026-10-444"
c = TestClient(app)
c.post("/login/dev/1")


def chip(html):
    m = re.search(r"Step (\d+) of (\d+) · ([^<]+)</span>", html)
    return m.group(3).strip() if m else ""


def nav(html):
    return html.split('aria-label="This order"', 1)[1].split("</nav>", 1)[0]


def test_drawings_menu_twelve_products():
    """An order with 12 drawn products: one Drawings menu with 12 entries, 'Product N', a status each, no 'Drawing N' tabs."""
    c.get(A)
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        first = s.query(db.OrderLine).filter_by(order_id=o.id).order_by(db.OrderLine.no).first()
        for no in range(4, 13):
            s.add(db.OrderLine(order_id=o.id, no=no, name="Pedestal %d (450 x 600)" % no, qty=1))
        s.commit()
        for l in s.query(db.OrderLine).filter_by(order_id=o.id):
            if l.drawing is None:
                l.drawing = db.Drawing(status="preview", engine_version="v11", spec={}, files={})
        s.query(db.OrderLine).filter_by(order_id=o.id, no=12).one().drawing.status = "failed"
        s.commit()
    n = nav(c.get(A).text)
    assert n.count('role="menuitem"') == 12 and "Drawings" in n
    assert not re.search(r">Drawing \d+<", n) and "Product 1 · " in n and "Product 12 · Pedestal 12" in n
    assert "Draft" in n and "Could not draw" in n
    v = c.get(A + "/view/2").text
    assert "Drawings: Product 2" in nav(v) and "Drawing 2 of" not in v                  # the viewer: which product is open


def test_status_chips_issued_approved_changes():
    """Issued, Approved and Changes asked show in the menu (approved / locked and client-changes cases)."""
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        ls = {l.no: l for l in s.query(db.OrderLine).filter_by(order_id=o.id)}
        for no in (1, 2, 3):
            l = ls[no]
            r = db.Revision(line_id=l.id, number="Encore-Nov-2026-09-%02d" % no, rev="A", engine_version="v11", spec={"model": None}, files={})
            s.add(r); s.flush()
            l.drawing.spec, l.drawing.engine_version, l.drawing.made_at = {"model": None}, "v11", r.issued_at
            if no == 2:
                s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="approved", name="Mr Singh", how="email"))
            if no == 3:
                s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="changes", name="Mr Singh", how="portal", note="wider"))
        s.commit()
    n = nav(c.get(A).text)
    row = lambda no: re.search(r"Product %d · .*?</a>" % no, n, re.S).group(0)
    assert "Issued" in row(1) and "Rev A" in row(1)
    assert "Approved" in row(2) and "Changes asked" in row(3)


def test_search_clears_and_live():
    """Clearing the search shows all projects at once; the boxes filter as you type (data-live) on desk and portal."""
    c.get("/orders/SAMPLE-REPEAT-1")
    h = c.get("/projects?q=SAMPLE").text
    assert "SAMPLE-REPEAT-1" in h and "Q-2026-10-444" not in h
    h = c.get("/projects?q=").text
    assert "SAMPLE-REPEAT-1" in h and "Q-2026-10-444" in h
    assert 'type="search" data-live id="q"' in h and "desk-live-search" in h
    n = c.get("/new").text
    assert n.count("data-live id=") == 1 and "data-qsearch" in n                    # quotation box: live results (issue #86)
    portal_past = open(os.path.join(os.path.dirname(__file__), "..", "desk", "templates", "portal", "past.html")).read()
    assert "data-live" in portal_past


def test_chip_matches_next_step_bar():
    """The chip names the current step as an action, in the next-step bar's words; ticked steps keep the past tense."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    ref = "SAMPLE-REPEAT-1"
    o = load(ref)
    q = sum(l.open_count for l in o.lines)
    if q:
        h = c.get("/orders/" + ref).text
        assert chip(h) == guide(o)["title"] == "Answer %d question%s" % (q, "" if q == 1 else "s")
        assert "All questions answered" not in h.split("Order checklist", 1)[1].split("</summary>", 1)[0]
        with db.Session() as s:
            for qq in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == ref):
                qq.answer = (qq.options or ["1"])[0]; qq.answered_by = "Kushal Singhal"; qq.answered_at = db.now()
            s.commit()
    o = load(ref)
    assert chip(c.get("/orders/" + ref).text) == guide(o)["title"] == "Draw the previews"
    c.post("/orders/%s/draw" % ref)
    h = c.get("/orders/" + ref).text
    items = steps(load(ref))
    assert next(x for x in items if x["title"] == "All questions answered")["state"] == "done"
    assert "All questions answered" in h                                                   # ticked steps: past tense
    assert chip(h) == summary(items)[0]["action"] and chip(h) != summary(items)[0]["title"]
    if summary(items)[0]["state"] != "blocked":                                          # a blocked step shows red, with its reason
        assert chip(h) == guide(load(ref))["title"]                                     # same words as the next-step bar


def test_old_order_unchanged():
    """An order with nothing drawn has no Drawings menu; the other tabs are as before."""
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        for l in s.query(db.OrderLine).filter_by(order_id=o.id):
            if l.drawing:
                s.delete(l.drawing)
        s.commit()
    n = nav(c.get("/orders/SAMPLE-REPEAT-1").text)
    assert "drawmenu" not in n and "Overview" in n and "Issue and approval" in n


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
