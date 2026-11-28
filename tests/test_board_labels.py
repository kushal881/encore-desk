"""Board labels (issue #61): the project page's button for one label per full board, made by the approved engines
(board_cutting.py --board-labels, engines issue #38). Until that engine version is approved the card says it is waiting;
no BOM yet → the button explains; a change of issued drawings makes the next set (P2) and withdraws the old labels;
salespeople cannot make them. The engine command is stood in for by a small script with the agreed output. Needs ENGINES_DIR."""
import os, sys, tempfile, json, shutil, textwrap
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines

A = "/orders/Q-2026-10-444"
c = TestClient(app)

STUB = textwrap.dedent('''
    import sys, os, json, pymupdf
    def board_labels(): pass
    args = sys.argv[1:]
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--out="))
    client = next(a.split("=", 1)[1] for a in args if a.startswith("--client="))
    json.dump(dict(args=args, files=sorted(os.listdir(os.path.join(client, "Other")))), open(os.path.join(out, "args.json"), "w"))
    d = pymupdf.open()
    for n in range(3):
        d.new_page(width=170, height=113).insert_text((10, 30), "Board %d of 3" % (n + 1))
    d.save(os.path.join(out, "Board labels.pdf"))
    print("BOARDS 3")
    print("MATERIAL\\t18 mm MDF\\t2")
    print("MATERIAL\\t25 mm MDF\\t1")
''')


@pytest.fixture
def new_engines(monkeypatch):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tool"))
    open(os.path.join(d, "tool", "board_cutting.py"), "w").write(STUB)
    monkeypatch.setattr(engines, "ENGINES_DIR", d)
    return d


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def _issue_and_release():
    as_(1); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "6", "nos": "1,2,3", "back": "issue"})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        for k in ("sent", "approved", "released"):
            s.add(db.Step(order_id=o.id, kind=k, by="test"))
        s.commit()
        return o.id


def test_board_labels(new_engines):
    if not os.environ.get("ENGINES_DIR"):
        pytest.skip("ENGINES_DIR not set")
    real = os.environ["ENGINES_DIR"]
    engines.ENGINES_DIR = real                                                  # drawing and issuing on the real engines
    oid = _issue_and_release()
    # engines too old: waiting message, and the route refuses with the reason
    h = c.get(A).text
    assert "Waiting for the engines: approve the board labels proposal" in h
    r = c.post(A + "/board-labels")
    assert r.status_code == 400 and "no board labels yet" in r.text
    engines.ENGINES_DIR = new_engines
    # no BOM yet: the card and the route explain
    assert "Make the bill of material first" in c.get(A).text
    assert "Make the bill of material first" in c.post(A + "/board-labels").text
    with db.Session() as s:
        s.add(db.ProductionFile(order_id=oid, kind="bom", panel_saw=True, path="/nowhere.xlsx", name="BOM.xlsx", revisions="x")); s.commit()
    assert "Make board labels" in c.get(A).text
    # a salesperson cannot make them (and the production screen gate refuses the path)
    with db.Session() as s:
        s.add(db.User(email="sales.ravi@encoreprimefurniture.com", name="Ravi", role="sales")); s.commit()
        sales = s.query(db.User).filter_by(name="Ravi").one().id
    as_(sales)
    assert c.post(A + "/board-labels").status_code == 403
    # made: one PDF, N boards by material, no client name passed as a label field, kept with the production files
    as_(1)
    r = c.post(A + "/board-labels", follow_redirects=False)
    assert r.status_code == 303
    with db.Session() as s:
        f = s.query(db.ProductionFile).filter_by(kind="boardlabels").one()
        assert f.summary == "3 boards · 18 mm MDF: 2 · 25 mm MDF: 1" and f.name.endswith("Board labels P1 (3 boards).pdf")
        got = json.load(open(os.path.join(os.path.dirname(f.path), "args.json")))
        assert "--board-labels" in got["args"] and not any(a.startswith("--replaces") for a in got["args"])
        assert any(a.startswith("--job=") for a in got["args"]) and len(got["files"]) == 3
    h = c.get(A).text
    assert "Board labels P1 (3 boards).pdf" in h and "Print again" in h and "3 boards · 18 mm MDF: 2" in h
    assert c.get("/production-files/%d" % f.id).content[:4] == b"%PDF"
    # print again for the same set: the same P
    c.post(A + "/board-labels")
    with db.Session() as s:
        assert [x.name.split("Board labels ")[1][:2] for x in s.query(db.ProductionFile).filter_by(kind="boardlabels")] == ["P1", "P1"]
    # the issued drawings change (a new revision): P2 replaces P1, which is shown withdrawn
    with db.Session() as s:
        l = s.query(db.OrderLine).filter_by(order_id=oid, no=1).one()
        r1 = l.revisions[-1]
        s.add(db.Revision(line_id=l.id, number=r1.number, rev="B", engine_version=r1.engine_version, spec=r1.spec, files=r1.files)); s.commit()
    c.post(A + "/board-labels")
    with db.Session() as s:
        last = s.query(db.ProductionFile).filter_by(kind="boardlabels").order_by(db.ProductionFile.id.desc()).first()
        got = json.load(open(os.path.join(os.path.dirname(last.path), "args.json")))
        assert "P2" in last.name and "--replaces=P1" in got["args"]
        assert any("labels of P1 are withdrawn" in m.text for m in s.query(db.Message).filter_by(order_id=oid))
    h = c.get(A).text
    assert "P1 withdrawn" in h and "Board labels P2" in h


def test_production_screen_reads_only_the_bom():
    """The production screen's BOM tab reads the bill of material, not a later cutting or label PDF (old data: files of every kind)."""
    import inspect
    from desk import production
    assert 'db.ProductionFile.kind == "bom"' in inspect.getsource(production._page)


def test_empty_values(new_engines):
    assert engines.has_board_labels(new_engines) and not engines.has_board_labels("/nowhere")        # "" = the engines in use
    from desk.app import board_label_sets
    assert board_label_sets(0) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
