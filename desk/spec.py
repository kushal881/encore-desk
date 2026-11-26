"""Builds the client specification (the product .json the engine reads) for one order line: the approved template for that
product plus what the order and the answers say. This file never changes how a product is drawn; it only fills in a specification."""
import json, os, re, math, datetime, copy

TEMPLATES = {("workstation", "loop"): "item_workstation_loop_leg.json", ("workstation", "c"): "item_workstation_c_leg.json",
             ("workstation", "tilted"): "item_workstation_tilted_leg.json", ("storage", ""): "item_cabinet_shutters_partition_shelves.json",
             ("pedestal", "1d1s"): "item_pedestal_1drawer_1shutter.json", ("pedestal", "2d"): "item_pedestal_2drawer.json",
             ("pedestal", "3d"): "item_pedestal_3drawer.json"}


class CannotDraw(Exception):
    """The desk has no approved way to draw this line. Shown to the user as it is; never worked around."""


def _mm(text, default=None):
    m = re.search(r"(\d+)\s*mm", text or "", re.I)
    return int(m.group(1)) if m else default


def variant(family, name, fields):
    low = name.lower()
    if family == "workstation":
        return "c" if re.search(r"\bc[- ]?leg", low) else "tilted" if "tilted" in low else "loop"
    if family == "pedestal":
        d = re.search(r"(\d)\s*drawer", low)
        if "shutter" in low:
            return "1d1s"
        return {"2": "2d", "3": "3d"}.get(d.group(1) if d else "", "")
    return ""


def build(engines_dir, order, line, read, answers, serial):
    """-> the specification dict. answers = {question key: answer text}."""
    family, fields, name = read["family"], read["fields"], line.name
    key = (family, variant(family, name, fields))
    if key not in TEMPLATES:
        raise CannotDraw("No approved template for this product yet (%s). This needs an engine change." % (name.split("(")[0].strip()))
    size = read["size"]
    if "size" in answers:
        m = re.search(r"L\s*(\d+)\D+D\s*(\d+)\D+H\s*(\d+)", answers["size"], re.I)
        if not m:
            raise CannotDraw('I could not read a size from the answer "%s". Write it as L1200 D450 H750.' % answers["size"])
        size = tuple(int(v) for v in m.groups())
    if not size:
        raise CannotDraw("No size for this line.")
    L, D, H = size
    spec = json.load(open(os.path.join(engines_dir, "templates", TEMPLATES[key]), encoding="utf-8"))
    spec = copy.deepcopy(spec); spec.pop("_comment", None)
    m = spec["model"]
    m.update(L=L, D=D, H=H)
    if family == "workstation":
        tt = _mm(fields.get("table top"), m.get("top_t", 25))
        m.update(top_t=tt, frame_L=L - 20, frame_D=D - 20, frame_H=H - tt)
        p = re.search(r"(\d+)\s*[*xX]\s*(\d+)", fields.get("under structure", ""))
        if p:
            m["pipe"] = sorted(int(v) for v in p.groups())
        if "grommet" in m:
            m["grommet"]["y"] = D - 100
        s = re.search(r"screen\s*L\s*(\d+)\s*H\s*(\d+)", name, re.I)
        if s and "screen" in m:
            m["screen"].update(w=int(s.group(1)), h=int(s.group(2)))
        r = re.search(r"(\d+)", fields.get("raceway length", ""))
        if r and "raceway" in m:
            m["raceway"]["L"] = int(r.group(1))
    elif family == "storage":
        m["t"] = _mm(fields.get("material"), m.get("t", 18))
        m["shutters"] = max(2, math.ceil(L / 500))
        if m["shutters"] != 3:
            m.pop("partition_after", None)
        if answers.get("shelves", "").strip().isdigit():
            m["shelves"] = int(answers["shelves"])
    elif family == "pedestal":
        m["t"] = _mm(fields.get("material"), m.get("t", 18))
        if answers.get("hinge"):
            m["hinge"] = answers["hinge"].strip().lower()
        if "shutter_h" in m and H != 650:
            m["shutter_h"] = H - 180
    edge = (fields.get("edge band") or fields.get("edgeband") or "").upper()
    if edge and "edgeband" in m:
        m["edgeband"] = edge
    lam = (fields.get("laminate") or "").upper()
    fab = (fields.get("fabric") or "").upper()
    for sw in spec.get("swatches", []):
        use = sw.get("use", "").lower()
        if lam and ("laminate" in use or "plpb" in use or "carcass" in use):
            sw["code"] = lam
        if fab and "fabric" in use:
            sw["code"] = fab
        sw.pop("image", None)
    for hw in spec.get("hardware", []):
        hw["image"] = None
    today = datetime.date.today().strftime("%d %b %Y").upper()
    spec["client"] = dict(company=order.client.name, delivery=list(order.delivery) or [""], drawing_id=order.source_ref)
    spec["item"] = dict(serial="%02d" % serial, product_name=name.split("(")[0].strip(), product_code="%d x %d x %d" % (L, D, H), qty=int(line.qty))
    if read["area"]:
        spec["item"]["area"] = read["area"]
    spec["drawing"].update(quotation=order.source_ref, date=today, rev="A", status="PREVIEW - NOT ISSUED")
    spec["revisions"] = [dict(rev="A", date=today, desc="Preview from the drawing desk", by="")]
    spec["numbering"] = dict(folder="DESK", serial="%02d" % serial, company_short=order.client.name)
    spec["notes_from_answers"] = {k: v for k, v in answers.items() if k == "note"}
    return spec
