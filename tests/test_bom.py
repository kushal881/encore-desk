"""The bill of material on the production step: made by the approved BOM engine from the latest issued revisions, with
or without the Panel-saw cutting switch (the engine's optional panel_saw rules). Needs ENGINES_DIR (an approved version
with the panel-saw rules for the switch test)."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

A = "/orders/Q-2026-10-444"
c = TestClient(app)


def _cells(path):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    return {c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row if isinstance(c.value, str)}


def _panel_saw_engines():
    return os.path.exists(os.path.join(engines.ENGINES_DIR or "", "tool", "bom.py")) and \
        "panel_saw" in open(os.path.join(engines.ENGINES_DIR, "tool", "bom.py"), encoding="utf-8").read()


def test_bill_of_material(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/1"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "6"})
    assert c.post(A + "/bom").status_code == 400                                       # not released yet
    with db.Session() as s:                                                             # empty: released, but nothing issued
        s.add(db.Order(client_id=s.query(db.Order).first().client_id, source="sample", source_ref="EMPTY-1")); s.commit()
        s.add(db.Step(order_id=s.query(db.Order).filter_by(source_ref="EMPTY-1").one().id, kind="released")); s.commit()
    assert "Nothing has been issued yet" in c.post("/orders/EMPTY-1/bom").text
    assert "Make the bill of material" not in c.get(A).text
    c.post(A + "/step/sent"); c.post(A + "/step/approved", data={"note": "Mr Singh, by email"}); c.post(A + "/step/released")
    h = c.get(A).text
    assert "Bill of material and cut list" in h and "Panel-saw cutting" in h and "Make the bill of material" in h

    # without the switch: boards counted as before
    c.post(A + "/bom")
    with db.Session() as s:
        f1 = s.query(db.ProductionFile).one()
    assert not f1.error and not f1.panel_saw and os.path.exists(f1.path)
    assert f1.revisions == "Encore-Nov-2026-06-01, Encore-Nov-2026-06-02, Encore-Nov-2026-06-03"
    cells = _cells(f1.path)
    assert "CUT length (mm)" not in cells and any("Storage" in v for v in cells)           # every issued product is in it
    r = c.get("/production-files/%d" % f1.id)
    assert r.status_code == 200 and r.content[:2] == b"PK"                                  # an Excel workbook

    # with the switch: the engine's panel-saw rules (cut sizes, grain)
    if _panel_saw_engines():
        c.post(A + "/bom", data={"panel_saw": "1"})
        with db.Session() as s:
            f2 = s.query(db.ProductionFile).order_by(db.ProductionFile.id.desc()).first()
        assert not f2.error and f2.panel_saw and f2.name.endswith("(panel saw).xlsx")
        cells = _cells(f2.path)
        assert "CUT length (mm)" in cells and "Grain along" in cells
        h = c.get(A).text
        assert "Q-2026-10-444-Bill of Material (panel saw).xlsx" in h and 'class="tag info">Panel-saw cutting' in h
        assert 'name="panel_saw" value="1" checked' in h                                # the order remembers the choice
    assert "Bill of material made by Kushal Singhal." in c.get(A + "/work").text

    # the engine refuses: said plainly, nothing to download
    monkeypatch.setattr(engines, "bom", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("STOP - ENGINE FILE NOT APPROVED: bom.py")))
    c.post(A + "/bom")
    with db.Session() as s:
        f3 = s.query(db.ProductionFile).order_by(db.ProductionFile.id.desc()).first()
    assert f3.error.startswith("STOP") and c.get("/production-files/%d" % f3.id).status_code == 404
    assert "Not made" in c.get(A).text
    c.post("/logout")
    assert c.get("/production-files/%d" % f1.id, follow_redirects=False).headers["location"] == "/login"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
