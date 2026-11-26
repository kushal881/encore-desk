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


def build(engines_dir, order, line, read, answers, serial, changes=None, finish=None, issue=None):
    """-> the specification dict. answers = {question key: answer text}; changes = confirmed change values;
    finish(text, kind) -> a shade from the finishes library; issue = dict(number folder, serial, revisions) when issuing."""
    changes = changes or {}
    family, fields, name = read["family"], read["fields"], line.name
    if family == "other" and answers.get("family", "").strip().lower() in ("workstation", "storage", "pedestal"):
        family = answers["family"].strip().lower()
    key = (family, variant(family, name, fields))
    if key not in TEMPLATES:
        raise CannotDraw("No approved template for this product yet (%s). This needs an engine addition; you can ask for one below." % (name.split("(")[0].strip()))
    size = read["size"]
    if "size" in answers:
        m = re.search(r"L\s*(\d+)\D+D\s*(\d+)\D+H\s*(\d+)", answers["size"], re.I)
        if not m:
            raise CannotDraw('I could not read a size from the answer "%s". Write it as L1200 D450 H750.' % answers["size"])
        size = tuple(int(v) for v in m.groups())
    if not size:
        raise CannotDraw("No size for this product.")
    L, D, H = (changes.get(k, v) for k, v in zip("LDH", size))
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
        if "screen" in m:
            m["screen"].update({k: changes[c] for c, k in (("screen_w", "w"), ("screen_h", "h")) if c in changes})
        if "raceway" in m and "raceway_L" in changes:
            m["raceway"]["L"] = changes["raceway_L"]
    elif family == "storage":
        m["t"] = _mm(fields.get("material"), m.get("t", 18))
        m["shutters"] = max(2, math.ceil(L / 500))
        if m["shutters"] != 3:
            m.pop("partition_after", None)
        if answers.get("shelves", "").strip().isdigit():
            m["shelves"] = int(answers["shelves"])
        if "shutters" in changes:
            m["shutters"] = changes["shutters"]; m.pop("partition_after", None)
        if "shelves" in changes:
            m["shelves"] = changes["shelves"]
    elif family == "pedestal":
        m["t"] = _mm(fields.get("material"), m.get("t", 18))
        if answers.get("hinge"):
            m["hinge"] = answers["hinge"].strip().lower()
        if "hinge" in changes:
            m["hinge"] = changes["hinge"]
        if "shutter_h" in m and H != 650:
            m["shutter_h"] = H - 180
    edge = (fields.get("edge band") or fields.get("edgeband") or "").upper()
    if edge and "edgeband" in m:
        m["edgeband"] = edge
    lam = (fields.get("laminate") or "").upper()
    fab = (fields.get("fabric") or "").upper()
    for sw in spec.get("swatches", []):
        use = sw.get("use", "").lower()
        text, kind = (lam, "laminate") if lam and ("laminate" in use or "plpb" in use or "carcass" in use) and "white" not in sw.get("code", "").lower() \
            else (fab, "fabric") if fab and "fabric" in use else ("", "")
        sw.pop("image", None)
        if not text:
            continue
        sw["code"] = text
        f = finish(text, kind) if finish else None
        if f is not None:
            sw["hex"] = f.hex
            rgb = [int(f.hex[i:i + 2], 16) for i in (1, 3, 5)]
            if kind == "laminate" and "top_rgb" in m:
                m["top_rgb"] = rgb
            if kind == "fabric" and isinstance(m.get("screen"), dict):
                m["screen"]["rgb"] = rgb
            if f.source == "estimate":
                sw["use"] = sw.get("use", "") + " (colour indicative)"
    for hw in spec.get("hardware", []):
        hw["image"] = None
    spec["item"] = dict(serial="%02d" % serial, product_name=name.split("(")[0].strip(), product_code="%d x %d x %d" % (L, D, H), qty=int(line.qty))
    if read["area"]:
        spec["item"]["area"] = read["area"]
    _header(spec, order, serial, issue)
    spec["notes_from_answers"] = {k: v for k, v in answers.items() if k == "note"}
    return spec


def _header(spec, order, serial, issue):
    """The parts of a specification that belong to this order and this issue, not to the product."""
    today = datetime.date.today().strftime("%d %b %Y").upper()
    spec["client"] = dict(company=order.client.name, delivery=list(order.delivery) or [""], drawing_id=order.source_ref)
    if issue:
        spec["drawing"].update(quotation=order.source_ref, date=issue["revisions"][0]["date"], rev=issue["revisions"][-1]["rev"], status="FOR APPROVAL", issued=True)
        spec["revisions"] = issue["revisions"]
        spec["numbering"] = dict(folder=issue["folder"], serial=issue["serial"], company_short=order.client.name)
        spec["item"]["serial"] = issue["serial"]
    else:
        spec["drawing"].update(quotation=order.source_ref, date=today, rev="A", status="PREVIEW - NOT ISSUED", issued=False)
        spec["revisions"] = [dict(rev="A", date=today, desc="Preview from the drawing desk", by="")]
        spec["numbering"] = dict(folder="DESK", serial="%02d" % serial, company_short=order.client.name)


def remake(order, line, past, serial, issue=None):
    """A product made again exactly as a past issued drawing: the product part of that drawing's specification,
    under this order's client details, quantity, number and date."""
    spec = copy.deepcopy(past)
    for k in ("revisions", "numbering", "notes_from_answers", "client"):
        spec.pop(k, None)
    spec["item"] = dict(past["item"], serial="%02d" % serial, qty=int(line.qty))
    _header(spec, order, serial, issue)
    spec["notes_from_answers"] = {}
    return spec
