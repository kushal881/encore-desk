"""Which part a numbered marker sits on. The engine writes where every part is on the sheet (its optional part map,
<drawing>.parts.json); the desk only reads that file and puts the engine's part names into plain words.
No drawing is made or changed here."""
import json, os, re

VIEWS = dict(front="front view", side="side view", top="top view", iso="isometric")
SKIP = ("hole", "bore", "drill", "opening")                # holes and pipe ends: point at the part they are in

# engine part name (start) -> plain words. The first rule that matches wins; a name with no rule is shown as written.
WORDS = [
    (r"uj_", "Beam joint"), (r"screen_bolt", "Screen bolt"), (r"screen_al|screen_panel|screen\d*$", "Screen"),
    (r"bracket", "Screen bracket"), (r"grommet", "Cable grommet"), (r"raceway", "Raceway"), (r"modesty", "Modesty panel"),
    (r"top\d*$|unit_top", "Top"), (r"loop\d+_adj\d+", "Adjustor"), (r"loop\d+_rail", "Leg rail"), (r"loop\d+_endplate", "Leg end plate"),
    (r"loop\d+", "Leg"), (r"leg", "Leg"), (r"foot|unit_foot", "Foot"), (r"castor", "Castor"),
    (r"front_support", "Front support"), (r"rear_support", "Rear support"), (r"beam_front", "Front beam"), (r"beam_rear", "Rear beam"),
    (r"rail_", "Rail"), (r"apron", "Apron"), (r"column", "Column"), (r"weight_plate", "Weight plate"), (r"base$", "Base"),
    (r"(unit_b\d+_)?d\d+_slide|slide", "Drawer channel"), (r"(unit_b\d+_)?(d\d+_|drawer\d*_)(bottom|box|side)", "Drawer box"),
    (r"(unit_b\d+_)?drawer_front", "Drawer front"), (r"(unit_b\d+_)?handle", "Handle"), (r"(unit_b\d+_)?lock", "Lock"),
    (r"(unit_b\d+_)?shutter", "Shutter"), (r"(unit_b\d+_)?shelf", "Shelf"), (r"(unit_b\d+_)?divider", "Divider"), (r"partition", "Partition"),
    (r"(unit_)?skirting", "Skirting"), (r"unit_side|side_[LR]$|left$|right$", "Side panel"), (r"(unit_)?back\d*$", "Back panel"),
    (r"(unit_)?bottom$", "Bottom panel"), (r"arm|tarm", "Arm"), (r"seat_deck|cushion", "Seat"), (r"tback|back", "Back"),
    (r"stud", "Stud"), (r"button|crease|pleat|stitch|tside", "Upholstery detail"),
]


def plain_name(part):
    """'loop0_front' -> 'Leg'; 'screen' -> 'Screen'. Left and right are added where the engine says so."""
    for pat, words in WORDS:
        if re.match(pat, part):
            side = " (left)" if re.search(r"_L$", part) else " (right)" if re.search(r"_R$", part) else ""
            return words + side
    return part.replace("_", " ").strip().capitalize()


def load(path):
    """The part map of a drawing, or None (engines without the part map, or a drawing made before it)."""
    if not path or not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))["parts"]
    except (ValueError, KeyError):
        return None


def _inside(x, y, poly):
    """Point in polygon (even-odd), for outlines given as [[x, y], ...]."""
    hit, n = False, len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def _near(x, y, b, pad):
    return b[0] - pad <= x <= b[2] + pad and b[1] - pad <= y <= b[3] + pad


def part_at(parts, x, y, pad=0.004):
    """The part under a point of the sheet (x, y: fractions of the sheet from its top-left corner, as markers are kept).
    The part seen in front wins: drawn solid before dashed, then the one drawn last in that view. A point just beside a
    thin part (a line, a pipe drawn as a line) still finds it. -> dict(part, view, name, where) or None."""
    if not parts:
        return None
    hits = []
    for q in parts:
        if q.get("kind") in SKIP or not _near(x, y, q["box"], pad):
            continue
        poly = q["outline"]
        exact = len(poly) >= 3 and _inside(x, y, poly)
        thin = (q["box"][2] - q["box"][0]) < 2 * pad or (q["box"][3] - q["box"][1]) < 2 * pad
        if exact or thin:
            hits.append((exact, not q.get("hidden"), q.get("order", 0), q))
    if not hits:
        return None
    _, _, _, q = max(hits, key=lambda h: (h[1], h[0], h[2]))
    name = plain_name(q["part"])
    return dict(part=q["part"], view=q["view"], name=name, where="%s (%s)" % (name, VIEWS.get(q["view"], q["view"])))
