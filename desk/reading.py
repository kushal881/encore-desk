"""Reads one order line as Encore writes it ("Label : value" lines) and lists what must be asked before drawing.
Deterministic: no AI here. Claude is added later only for free text this cannot read."""
import re

SIZE = re.compile(r"L\s*(\d+)\s*[xX*,]?\s*D\s*(\d+)\s*[xX*,]?\s*H\s*(\d+)", re.I)
# product type from the line's name, first match wins (a conference table is checked before "Vertex" means a director table)
FAMILIES = (("conference", (r"conference", r"meeting\s+table", r"board\s*room")),
            ("pedestal", (r"pedestal",)), ("workstation", (r"workstation", r"work\s+station")), ("storage", (r"storage", r"cabinet", r"almirah")),
            ("manager_table", (r"director", r"manager", r"executive\s+table", r"vertex")),
            ("centre_table", (r"cent(?:re|er)\s+table", r"coffee\s+table")), ("side_table", (r"side\s+table",)),
            ("desk", (r"\bdesk\b", r"office\s+table")))
# the product-type question: what the user sees -> the type (only types with an approved template, plus conference tables)
TYPE_OPTIONS = (("Workstation", "workstation"), ("Storage", "storage"), ("Pedestal", "pedestal"),
                ("Director / manager table (Vertex, with side unit)", "manager_table"), ("Desk (panel legs)", "desk"),
                ("Centre table", "centre_table"), ("Side table", "side_table"), ("Conference table", "conference"))
UNIT_DEFAULT = "L900 D400 H750"


def size_of(text):
    m = SIZE.search(text or "")
    return tuple(int(v) for v in m.groups()) if m else None


def fmt(size):
    return "L%d D%d H%d" % size


def read_line(name, description):
    """-> dict(family, size, fields {label: value}, area, notes [..], questions [dict(key, text, options)])"""
    fields, loose = {}, []
    for raw in (description or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            fields[" ".join(k.split()).lower()] = v.strip().rstrip(".")
        else:
            loose.append(s)
    low = name.lower()
    family = next((f for f, keys in FAMILIES if any(re.search(k, low) for k in keys)), "other")
    title_size, body_size = size_of(name), size_of(fields.get("size", ""))
    size = body_size or title_size
    qs = []
    if title_size and body_size and title_size != body_size:
        diff = [("L", "D", "H")[i] for i in range(3) if title_size[i] != body_size[i]]
        qs.append(dict(key="size", text="The title says %s and the size line says %s (%s differs). Which is correct?" % (fmt(title_size), fmt(body_size), ", ".join(diff)),
                       options=[fmt(title_size), fmt(body_size)]))
    if not size:
        qs.append(dict(key="size", text="No size found on this line. What is the size (L, D, H in mm)?", options=[]))
    note = fields.get("note") or fields.get("custom")
    if note:
        qs.append(dict(key="note", text='The note says "%s". What does it mean for the drawing?' % note, options=[]))
    if family == "storage" and "shelves" not in fields:
        qs.append(dict(key="shelves", text="How many shelves inside?", options=["0", "1", "2"]))
    if family == "pedestal" and "shutter" in low and "hinge" not in fields:
        qs.append(dict(key="hinge", text="Which side is the shutter hinged?", options=["Left", "Right"]))
    if family == "manager_table" and not size_of(fields.get("side unit", "")):
        qs.append(dict(key="unit", text="What is the size of the side unit (L, D, H in mm)?", options=[UNIT_DEFAULT]))
    if family == "other":
        qs.append(dict(key="family", text="I could not tell the product type from the name. Which product is this?", options=[t for t, _ in TYPE_OPTIONS] + ["None of these"]))
    return dict(family=family, size=size, fields=fields, area=loose[0] if loose else "", notes=[note] if note else [], questions=qs)
