"""Table types the approved engines have a template for: director / manager table (Vertex, with side unit), desk (panel
legs), centre table, side table. Conference tables have engine builders but no approved template yet: refused plainly.
The drawing tests need ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import pytest
from desk.reading import read_line, TYPE_OPTIONS
from desk import spec, engines, db

ORDER = NS(client=NS(name="Test Client Pvt Ltd"), delivery=["Gurugram"], source_ref="Q-TEST-1")


def fam(name, description=""):
    return read_line(name, description)["family"]


def test_types_from_the_name():
    assert fam("Director Table (Vertex) L1800 D900 H750") == "manager_table"
    assert fam("Vertex Table with Side Unit") == "manager_table" and fam("Manager table") == "manager_table"
    assert fam("Vertex Conference Table L3600 D1200 H750") == "conference"         # conference wins over "Vertex"
    assert fam("Meeting table round") == "conference" and fam("Boardroom table") == "conference"
    assert fam("Centre Table L1200 D600 H450") == "centre_table" and fam("Coffee table") == "centre_table"
    assert fam("Side Table") == "side_table" and fam("Office Desk L1400 D700 H750") == "desk"
    assert fam("Workstation EDS 02") == "workstation" and fam("Storage ESP 01") == "storage" and fam("Pedestal 2 drawer") == "pedestal"
    assert fam("Desktop monitor arm") == "other"


def test_type_question_offers_the_tables():
    q = next(q for q in read_line("Reception counter", "")["questions"] if q["key"] == "family")
    assert q["options"] == [t for t, _ in TYPE_OPTIONS] + ["None of these"]
    assert "Director / manager table (Vertex, with side unit)" in q["options"] and "Conference table" in q["options"]
    qs = {q["key"]: q for q in read_line("Director Table L1800 D900 H750", "")["questions"]}
    assert qs["unit"]["options"] == ["L900 D400 H750"]                             # the side unit size is asked
    assert "unit" not in {q["key"] for q in read_line("Director Table L1800 D900 H750", "Side unit : L1200 D450 H750")["questions"]}


def _build(name, description="", answers=None):
    line = NS(name=name, description=description, qty=1)
    return spec.build(engines.ENGINES_DIR, ORDER, line, read_line(name, description), answers or {}, 1)


def test_conference_tables_are_refused_plainly():
    with pytest.raises(spec.CannotDraw) as e:
        _build("Vertex Conference Table L3600 D1200 H750")
    assert "no approved starter template" in str(e.value) and "engine addition" in str(e.value)


@pytest.mark.parametrize("name, description, answers, check", [
    ("Director Table (Vertex) L1800 D900 H750", "Side unit : L1200 D450 H750\nTable top : 25 mm", {},
     lambda m, it: (m["type"], m["L"], m["frame_L"], m["frame_H"], m["unit"]["L"], m["unit"]["D"], it["product_code"]) ==
                   ("manager_table", 1800, 1780, 725, 1200, 450, "1800 x 900 x 750 + Unit 1200 x 450 x 750")),
    ("Director Table L1600 D800 H750", "", {"unit": "L900 D400 H750"},
     lambda m, it: (m["unit"]["L"], m["unit"]["D"], m["unit"]["H"]) == (900, 400, 750)),
    ("Office Desk L1400 D700 H750", "", {}, lambda m, it: (m["type"], m["L"], m["D"], m["H"]) == ("desk", 1400, 700, 750)),
    ("Centre Table L1200 D600 H450", "", {}, lambda m, it: (m["type"], m["L"], m["D"], m["H"]) == ("centre_table", 1200, 600, 450)),
    ("Side Table", "", {"size": "L450 D450 H600"},
     lambda m, it: (m["type"], m["D"], m["H"], "L" in m, it["product_code"]) == ("side_table", 450, 600, False, "D450 x H600")),
    ("Reception counter", "Size : L1200 D600 H450", {"family": "Centre table"}, lambda m, it: m["type"] == "centre_table"),
])
def test_tables_are_drawn(name, description, answers, check):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    sp = _build(name, description, answers)
    assert check(sp["model"], sp["item"])
    files = engines.draw(sp, "tables/%s" % name.split()[0].lower())                  # the approved engine draws it, checks pass
    assert os.path.exists(files["pdf"]) and all(c["ok"] for c in files["checks"])


def test_old_questions_follow_the_rules():
    """A director table read by an earlier version was asked its type with three choices; opening it now drops that
    question and asks for the side unit instead. Answered questions are kept."""
    from fastapi.testclient import TestClient
    from desk.app import app
    c = TestClient(app)
    c.post("/login/dev/2")
    c.get("/orders/Q-2026-10-444")
    with db.Session() as s:
        o = s.query(db.Order).one()
        l = db.OrderLine(order_id=o.id, no=4, name="Director Table L1800 D900 H750", description="", qty=1)
        l.questions.append(db.Question(key="family", text="Which product is this?", options=["Workstation", "Storage", "Pedestal", "None of these"]))
        s.add(l)
        p = s.query(db.OrderLine).filter_by(no=2).one()
        kept = next(q for q in p.questions if q.key == "shelves")
        kept.answer = "2"
        s.commit()
    c.get("/orders/Q-2026-10-444")
    with db.Session() as s:
        qs = {q.key: q for q in s.query(db.OrderLine).filter_by(no=4).one().questions}
        assert set(qs) == {"unit"} and qs["unit"].options == ["L900 D400 H750"]
        assert next(q for q in s.query(db.OrderLine).filter_by(no=2).one().questions if q.key == "shelves").answer == "2"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
