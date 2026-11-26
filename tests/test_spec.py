"""Needs ENGINES_DIR to point at a copy of the approved engines."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
from desk.sources.sample import SampleSource
from desk.reading import read_line
from desk import spec, engines


def _order():
    o = SampleSource().get_order("Q-2026-10-444")
    return o, NS(client=NS(name=o.client_name), delivery=o.delivery, source_ref=o.ref)


def test_specifications():
    o, order = _order()
    ans = [{"note": "bracket"}, {"size": "L1200 D450 H750", "shelves": "1"}, {"hinge": "Left"}]
    m = [spec.build(engines.ENGINES_DIR, order, l, read_line(l.name, l.description), ans[i], i + 1)["model"] for i, l in enumerate(o.lines)]
    assert (m[0]["L"], m[0]["frame_L"], m[0]["frame_D"], m[0]["frame_H"], m[0]["pipe"], m[0]["grommet"]["y"]) == (1350, 1330, 730, 725, [25, 50], 650)
    assert (m[0]["screen"]["w"], m[0]["screen"]["h"], m[0]["raceway"]["L"]) == (900, 300, 600)
    assert (m[1]["type"], m[1]["D"], m[1]["shutters"], m[1]["shelves"]) == ("cabinet", 450, 3, 1)
    assert m[2]["hinge"] == "left"


def test_refuses_what_it_cannot_draw():
    o, order = _order()
    l = NS(name="Reception counter curved", description="Size: L3000 D800 H1100mm", qty=1)
    try:
        spec.build(engines.ENGINES_DIR, order, l, read_line(l.name, l.description), {}, 1)
    except spec.CannotDraw as e:
        assert "engine addition" in str(e)
    else:
        raise AssertionError("should refuse")


if __name__ == "__main__":
    test_specifications(); test_refuses_what_it_cannot_draw(); print("spec tests pass")
