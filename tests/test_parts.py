"""Markers on parts: the engine's part map (written beside the PDF when the product file has "part_map": true) tells which
part a marker sits on. The fixture is the engine's part map of the sample order's product 2 (storage unit, 3 shutters)."""
import os, sys, tempfile, json
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, parts

FIX = os.path.join(HERE, "fixtures", "sample_product2_parts.json")
MAP = parts.load(FIX)
A = "/orders/Q-2026-10-444"


def centre(name, view="front"):
    q = next(q for q in MAP if q["part"] == name and q["view"] == view)
    return (q["box"][0] + q["box"][2]) / 2, (q["box"][1] + q["box"][3]) / 2


def test_plain_names():
    assert parts.plain_name("loop0_front") == "Leg" and parts.plain_name("loop10_adj1_pad") == "Adjustor"
    assert parts.plain_name("screen") == "Screen" and parts.plain_name("screen_al_left") == "Screen"
    assert parts.plain_name("side_L") == "Side panel (left)" and parts.plain_name("rear_support_R") == "Rear support (right)"
    assert parts.plain_name("handle_shutter2") == "Handle" and parts.plain_name("unit_b1_shutter") == "Shutter"
    assert parts.plain_name("top") == "Top" and parts.plain_name("uj_beam_front1_x0_bolt2") == "Beam joint"
    assert parts.plain_name("something_new") == "Something new"                 # no rule yet: the engine's name, readable


def test_part_at():
    assert parts.part_at(None, 0.5, 0.5) is None and parts.part_at(MAP, 0.02, 0.02) is None     # nothing there
    x, y = centre("shutter1")
    assert parts.part_at(MAP, x, y)["where"] == "Shutter (front view)"          # the shutter in front, not the shelf behind it
    x, y = centre("handle_shutter2")
    assert parts.part_at(MAP, x, y)["name"] == "Handle"                         # the handle drawn over the shutter
    x, y = centre("top")
    hit = parts.part_at(MAP, x, y)
    assert hit["name"] == "Top" and hit["view"] == "front"
    x, y = centre("top", "iso")
    assert parts.part_at(MAP, x, y)["where"] == "Top (isometric)"
    q = next(q for q in MAP if q["part"] == "side_L" and q["view"] == "front")
    assert parts.part_at(MAP, q["box"][0] - 0.002, 0.40)["where"] == "Side panel (left) (front view)"   # just beside a thin panel still finds it


c = TestClient(app)


def test_markers_name_their_part():
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    with db.Session() as s:
        d = s.get(db.Drawing, 2)
        if not d.files.get("parts"):                                      # engines without the part map: use the sample's map
            d.files = dict(d.files, parts=FIX)
        s.commit()
    v = c.get(A + "/view/2").text
    assert "Leave empty to use the part it sits on" in v
    key = v.split('name="shown" value="')[1].split('"')[0]
    x, y = centre("shutter1")
    v = c.post(A + "/marks/2", data={"shown": key, "x": "%.2f" % (x * 100), "y": "%.2f" % (y * 100), "label": ""}).text
    assert 'value="Shutter (front view)"' in v and "On: Shutter (front view)" in v           # no note: the part's name
    x, y = centre("top", "iso")
    v = c.post(A + "/marks/2", data={"shown": key, "x": "%.2f" % (x * 100), "y": "%.2f" % (y * 100), "label": "make it white"}).text
    assert 'value="make it white"' in v and "On: Top (isometric)" in v
    h = c.post(A + "/change", data={"text": "At 1 and 2 make it 1500 long", "line": "2", "shown": key}).text
    assert "[Markers: 1 = Shutter (front view); 2 = make it white, on Top (isometric)]" in h
    # a marker on another sheet, or on a drawing without a part map, names no part
    v = c.post(A + "/marks/2", data={"shown": key + "s2", "x": "41", "y": "28", "label": "", "sheet": "2"}).text
    with db.Session() as s:
        assert s.query(db.Mark).filter_by(no=3).one().label == ""
        d = s.get(db.Drawing, 2); d.files = {k: v for k, v in d.files.items() if k != "parts"}; s.commit()
    v = c.get(A + "/view/2").text
    assert "On: " not in v and "For example: screen should be L800" in v


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
