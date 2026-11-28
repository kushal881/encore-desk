"""The drawing viewer: steps, automatic checks, mark ready to issue, sheets, markers per sheet, production preview
with placeholders. The page tests need ENGINES_DIR."""
import os, sys, tempfile, types
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, viewer

A = "/orders/Q-2026-10-444"
PASS_OUT = "DXF: x.dxf\nPDF: x.pdf\nScale 1:10\nISO TRUE VIEW: PASS\n"


# ---- rules, no engine needed ---------------------------------------------------------------------------------------
def test_checks_from_engine_output():
    ok = engines.checks_from(PASS_OUT, True)
    assert [c["key"] for c in ok] == ["clash", "true_view", "engine"] and all(c["ok"] for c in ok)
    clash = engines.checks_from("Traceback...\nValueError: CLASH: 'leg1' and 'beam_front' overlap (40 x 25 x 50 mm) - move one of them.", False)
    assert [c["ok"] for c in clash] == [False, None, None]
    assert clash[0]["note"].startswith("'leg1' and 'beam_front' overlap")
    tv = engines.checks_from("PDF: x.pdf\nISO TRUE VIEW: FAIL - 2 part(s) drawn over a part in front of them (do not send this drawing):\n  'a' is behind 'b'", False)
    assert [c["ok"] for c in tv] == [None, False, None]
    assert tv[1]["note"].startswith("2 part(s) drawn over")
    lock = engines.checks_from(" STOP - ENGINE FILE NOT APPROVED: encore_drawing.py\n", False)
    assert lock[2]["ok"] is False and lock[0]["ok"] is None
    assert engines.checks_from("PDF: x.pdf\n", True)[1]["ok"] is None          # made, but the true-view result was not printed


def test_check_rows_and_all_pass():
    rows = viewer.check_rows(None)
    assert [r["text"] for r in rows] == ["Not recorded"] * 3
    rows = viewer.check_rows(engines.checks_from(PASS_OUT, True))
    assert [r["state"] for r in rows] == ["pass"] * 3 and [r["text"] for r in rows] == ["Pass"] * 3
    assert viewer.all_pass(engines.checks_from(PASS_OUT, True))
    assert not viewer.all_pass(None) and not viewer.all_pass(engines.checks_from("CLASH: a", False))


def _line(status="preview", issued_current=False, open_count=0):
    return types.SimpleNamespace(drawing=types.SimpleNamespace(status=status) if status else None, issued_current=issued_current, open_count=open_count)


def test_step_of():
    assert viewer.step_of("drawing", _line(None, open_count=2)) == (2, "Specify")
    assert viewer.step_of("drawing", _line(None)) == (3, "Generate")
    assert viewer.step_of("drawing", _line("failed")) == (3, "Generate")
    assert viewer.step_of("drawing", _line()) == (4, "Review")
    assert viewer.step_of("sent", _line()) == (4, "Review")                      # a new preview after issue is back in review
    for stage, n in (("issued", 5), ("sent", 6), ("approved", 7), ("production", 7), ("completed", 8)):
        assert viewer.step_of(stage, _line(issued_current=True))[0] == n


def test_next_step():
    good = engines.checks_from(PASS_OUT, True)
    bad = engines.checks_from("CLASH: a and b overlap", False)
    l = _line()
    assert viewer.next_step(l, True, good, None, False, "client")["kind"] == "ready"
    assert viewer.next_step(l, True, good, types.SimpleNamespace(by="Suraj"), False, "client") == dict(kind="done", title="Ready to issue", text="Marked by Suraj.")
    assert viewer.next_step(l, True, good, None, True, "client")["kind"] == "confirm"
    assert viewer.next_step(l, True, None, None, False, "client")["kind"] == "redraw"
    assert viewer.next_step(l, True, bad, None, False, "client")["kind"] == "blocked"
    assert viewer.next_step(_line("failed"), False, bad, None, False, "client")["kind"] == "failed"
    assert viewer.next_step(_line(issued_current=True), False, good, None, False, "client")["kind"] == "info"
    assert viewer.next_step(l, True, good, None, False, "prod")["kind"] == "info"


def test_sheet_key_and_placeholders():
    assert viewer.sheet_key("d123", 1) == "d123" and viewer.sheet_key("r5", 3) == "r5s3"
    assert viewer.placeholders() == dict(job_order="JO-[NO.]", job_order_date="[DATE]", deadline="[DATE]")
    assert viewer.placeholders("JO 26-27-11-0119")["job_order"] == "JO 26-27-11-0119"


# ---- the page, with the engine -------------------------------------------------------------------------------------
c = TestClient(app)


def _draw():
    c.post("/login/dev/2")
    c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")


def _key(v):
    return v.split('data-key="')[1].split('"')[0]


def test_viewer_page():
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    _draw()
    v = c.get(A + "/view/2").text
    assert "Step 4 of 8 · Review" in v and "Product 2 · 2 of 3 drawn" in v and "Preview, not issued" in v
    assert v.count('class="tag ok">Pass</span>') == 3 and "Automatic checks" in v
    assert "Looks right — mark ready to issue" in v and 'id="zfit"' in v and "Sheet 1 / 1" in v
    assert 'href="/orders/Q-2026-10-444/view/1" aria-label="Previous product"' in v and 'href="/orders/Q-2026-10-444/view/3" aria-label="Next product"' in v
    assert c.get(A + "/view/1").text.count("Previous product") == 0
    assert c.get(A + "/view/2?s=9").status_code == 200                          # a sheet past the end shows the last sheet
    assert c.get("/drawings/2/big?page=0").status_code == 200 and c.get("/drawings/2/big?page=7").status_code == 404
    assert "Not checked yet" in c.get(A + "/work").text

    # markers are kept per sheet
    key = _key(v)
    c.post(A + "/marks/2", data={"shown": key, "x": "30", "y": "40", "label": "left shutter"})
    assert c.post(A + "/marks/2", data={"shown": key + "s2", "x": "50", "y": "50", "label": "on sheet 2", "sheet": "2"}).status_code == 200
    assert c.post(A + "/marks/2", data={"shown": key + "x2", "x": "50", "y": "50"}).status_code == 400
    v = c.get(A + "/view/2").text
    # pins are per sheet; the message box lists every marker not sent yet, a marker of another sheet with its sheet (issue #82)
    assert "left shutter" in v and v.count('class="pin" data-mark=') == 1 and "on sheet 2 <span class=\"sub\">(sheet 2)</span>" in v

    # mark ready: only the drawing shown, only when the checks pass
    assert c.post(A + "/view/2/ready", data={"shown": "d1"}).status_code == 400
    h = c.post(A + "/view/2/ready", data={"shown": key}).text
    assert "Ready to issue" in h and "Marked by Sushil Singhal." in h and "Go to issue" in h
    w = c.get(A + "/work").text
    assert "Checked, ready to issue" in w and "marked ready to issue by Sushil Singhal" in w

    # a change request waiting comes first
    c.post(A + "/change", data={"text": "make it 1500 long", "line": "3", "shown": ""})
    v3 = c.get(A + "/view/3").text
    assert "Confirm the change request first" in v3 and "mark ready to issue</button>" not in v3
    assert c.post(A + "/view/3/ready", data={"shown": _key(v3)}).status_code == 400


def test_failed_and_old_drawings():
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:
        d = s.query(db.Drawing).filter_by(id=1).one()
        d.status, d.error = "failed", "'leg1' and 'top' overlap (10 x 10 x 10 mm) - move one of them."
        d.files = {"checks": engines.checks_from("CLASH: 'leg1' and 'top' overlap (10 x 10 x 10 mm) - move one of them.", False)}
        s.commit()
    v = c.get(A + "/view/1").text
    assert "This drawing could not be made" in v and 'class="tag bad">Fail</span>' in v and "Not reached" in v
    assert "Production drawing</span>" in v and "+ Add numbered marker" not in v        # no production drawing, no markers
    assert c.post(A + "/view/1/ready", data={"shown": "d1"}).status_code == 400
    assert c.post(A + "/view/1/production").status_code == 400
    assert 'href="/orders/Q-2026-10-444/view/1"' in c.get(A + "/products").text              # still reachable from the Products tab

    # a preview made before the desk recorded checks: draw it again from the viewer
    with db.Session() as s:
        d = s.query(db.Drawing).filter_by(id=1).one()
        d.status, d.error, d.files = "preview", "", {k: v for k, v in d.files.items() if k != "checks"} or {}
        s.commit()
    r = c.post(A + "/draw", data={"no": "1"}, follow_redirects=False)
    assert r.headers["location"] == A + "/view/1"
    v = c.get(A + "/view/1").text
    assert v.count('class="tag ok">Pass</span>') == 3 and "Looks right — mark ready to issue" in v
    with db.Session() as s:
        d = s.query(db.Drawing).filter_by(id=1).one()
        d.files = {k: v for k, v in d.files.items() if k != "checks"}
        s.commit()
    v = c.get(A + "/view/1").text
    assert v.count("Not recorded") == 3 and "Draw again and check" in v
    assert c.post(A + "/view/1/ready", data={"shown": _key(v)}).status_code == 400


def test_production_preview():
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    v = c.get(A + "/view/2?mode=prod").text
    assert "No production drawing yet" in v and "Make a production preview" in v and "+ Add numbered marker" not in v
    sent, real = [], engines.draw_set
    engines.draw_set = lambda sp, key, edir=None: sent.append(sp) or real(sp, key, edir)       # see what the engine is given
    try:
        r = c.post(A + "/view/2/production", data={"rev": "0"}, follow_redirects=False)
    finally:
        engines.draw_set = real
    with db.Session() as s:
        jo = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().job_order     # a job order already in Odoo is used
    assert sent[0]["drawing_set"] == "PRODUCTION" and sent[0]["production"] == dict(job_order=jo or "JO-[NO.]", job_order_date="[DATE]", deadline="[DATE]")
    assert r.headers["location"] == A + "/view/2?mode=prod"
    v = c.get(A + "/view/2?mode=prod").text
    assert "Production preview — not for issue." in v and "Make a production preview" not in v and 'aria-current="page">Production drawing' in v
    with db.Session() as s:
        pp = s.query(db.ProdPreview).one()
        assert not pp.error and pp.pages >= 1 and s.query(db.ProductionSet).count() == 0      # never a real production set
        pid = pp.id
    assert c.get("/prodpreview/%d/page/0" % pid).headers["content-type"] == "image/png"
    assert c.get("/prodpreview/%d/pdf" % pid).status_code == 200 and c.get("/prodpreview/%d/page/9" % pid).status_code == 404
    c.post("/logout")
    assert c.get("/prodpreview/%d/pdf" % pid, follow_redirects=False).headers["location"] == "/login"
    assert c.post(A + "/view/2/ready", data={"shown": "d1"}, follow_redirects=False).headers["location"] == "/login"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
