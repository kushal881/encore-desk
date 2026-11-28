"""Cutting diagrams and panel labels on the production files step: made by the approved engines' cutting command
(tool/board_cutting.py), kept with the production files. Without that command in the approved engines, the step says it
waits for the engine approval. Needs ENGINES_DIR."""
import os, sys, tempfile, shutil, textwrap
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

c = TestClient(app)
A = "/orders/Q-2026-10-444"
FAKE = textwrap.dedent('''
    """Stands in for the engines' cutting command in this test (def make( is what the desk looks for)."""
    import sys, os, pymupdf
    def make(): pass
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    out = next(x.split("=", 1)[1] for x in sys.argv if x.startswith("--out="))
    cl = next(x.split("=", 1)[1] for x in sys.argv if x.startswith("--client="))
    if os.environ.get("FAKE_NOTHING"):
        print("No board panels in these products: nothing to cut on the panel saw."); sys.exit(0)
    assert sorted(os.listdir(os.path.join(cl, "Other"))) and a == sorted(a, key=int)
    for name in ("Encore-Nov-2026-05-Board cutting diagrams.pdf", "Encore-Nov-2026-05-Panel labels.pdf"):
        d = pymupdf.open(); d.new_page().insert_text((50, 50), name); d.save(os.path.join(out, name))
    print("%d labels" % (12 * len(a)))
''')


def fake_engines():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tool"))
    open(os.path.join(d, "tool", "board_cutting.py"), "w").write(FAKE)
    return d


def test_cutting(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw"); c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    assert c.post(A + "/cutting").status_code == 400                                           # not released yet
    with db.Session() as s:
        o = s.query(db.Order).one()
        s.add(db.Step(order_id=o.id, kind="approved", note="ok", by="Sushil Singhal")); s.commit()
    c.post("/login/dev/1"); c.post(A + "/step/released", data={"note": ""})
    # the approved engines here have no cutting command: the step says so, and pressing anyway records why
    if not engines.has_cutting():
        assert "Waiting for the engines" in c.get(A).text and "Make cutting diagrams and labels" not in c.get(A).text
        c.post(A + "/cutting")
        with db.Session() as s:
            assert "no cutting-diagram command yet" in s.query(db.ProductionFile).filter_by(kind="cutting").one().error
    # with the command: both PDFs made and kept with the production files, in the order's serial order
    monkeypatch.setattr(engines, "ENGINES_DIR", fake_engines())
    assert "Make cutting diagrams and labels" in c.get(A).text
    c.post(A + "/cutting")
    with db.Session() as s:
        made = s.query(db.ProductionFile).filter(db.ProductionFile.kind.in_(("cutting", "labels")), db.ProductionFile.error == "").all()
    assert {f.kind for f in made} == {"cutting", "labels"} and all(os.path.exists(f.path) for f in made)
    assert any(f.name.endswith("Panel labels (36).pdf") for f in made)                          # 3 products x 12 labels
    h = c.get(A).text
    assert "Board cutting diagrams.pdf" in h and "Panel labels (36).pdf" in h
    assert c.get("/production-files/%d" % made[0].id).status_code == 200
    # nothing to cut (sofas only): said plainly
    monkeypatch.setenv("FAKE_NOTHING", "1")
    c.post(A + "/cutting")
    with db.Session() as s:
        assert s.query(db.ProductionFile).filter_by(kind="cutting").order_by(db.ProductionFile.id.desc()).first().error.startswith("These products have no board panels")
    monkeypatch.delenv("FAKE_NOTHING")
    # a quotation change waiting: locked, as for the BOM and production drawings
    with db.Session() as s:
        s.add(db.QuoteChange(order_id=s.query(db.Order).one().id, version=2, kind="qty", name="x", stage="production")); s.commit()
    r = c.post(A + "/cutting")
    assert r.status_code == 400 and "quotation change" in r.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
