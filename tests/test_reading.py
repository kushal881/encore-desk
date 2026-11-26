import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desk.sources.sample import SampleSource
from desk.reading import read_line, size_of


def test_sample_order():
    o = SampleSource().get_order("Q-2026-10-444")
    r = [read_line(l.name, l.description) for l in o.lines]
    assert [x["family"] for x in r] == ["workstation", "storage", "pedestal"]
    assert [x["size"] for x in r] == [(1350, 750, 750), (1200, 450, 750), (400, 450, 650)]
    assert [[q["key"] for q in x["questions"]] for x in r] == [["note"], ["size", "shelves"], ["hinge"]]
    assert r[1]["questions"][0]["options"] == ["L1200 D400 H750", "L1200 D450 H750"]
    assert r[1]["area"] == "Office Block"
    assert r[0]["fields"]["laminate"] == "Laurel Oak Dark 3416"


def test_sizes():
    assert size_of("Size : L400 D450 H650mm") == (400, 450, 650)
    assert size_of("no size here") is None
    assert read_line("Pedestal 3 Drawer", "Size: L400 D450 H650mm")["questions"] == []


if __name__ == "__main__":
    test_sample_order(); test_sizes(); print("reading tests pass")
