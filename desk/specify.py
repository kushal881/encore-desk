"""The Specify screen's rules: areas per product, each product's status, and the summary of what will be built.
No drawing here: the summary is read from the specification the engine will be given."""

KEYS = (("shelves", "Shelves"), ("shutters", "Shutters"), ("hinge", "Hinge side"), ("partitions", "Partitions"),
        ("screen.w", "Screen width"), ("screen.h", "Screen height"), ("raceway.L", "Raceway length"), ("leg", "Legs"),
        ("top_t", "Top thickness"), ("edgeband", "Edge band"), ("unit.L", "Side unit length"), ("unit.D", "Side unit depth"),
        ("unit.H", "Side unit height"), ("unit.side", "Side unit at"))


def check_areas(rows, qty):
    """rows = [(area, qty text)] as typed. -> (clean [(area, qty)], problem or ""). Empty rows are dropped; no rows at all
    is allowed (the Area field stays blank). Otherwise every area needs a name and a whole quantity, and the quantities
    must add up to the product's quantity."""
    clean = []
    for area, q in rows:
        area, q = (area or "").strip(), (q or "").strip()
        if not area and not q:
            continue
        if not area:
            return [], "Every quantity needs an area name."
        try:
            n = float(q or "0")
        except ValueError:
            return [], "The quantity for %s is not a number." % area
        if n <= 0 or n != int(n):
            return [], "The quantity for %s must be a whole number above 0." % area
        clean.append((area[:120], int(n)))
    if clean and sum(n for _, n in clean) != qty:
        return [], "The area quantities add up to %d, but the product quantity is %g. They must be equal." % (sum(n for _, n in clean), qty)
    return clean, ""


def area_text(areas, qty):
    """What the sheet's Area field shows: one area for the whole quantity -> its name; several -> "Name (n), Name (n)"."""
    if not areas:
        return ""
    if len(areas) == 1 and areas[0][1] == qty:
        return areas[0][0]
    return ", ".join("%s (%d)" % (a, n) for a, n in areas)


def status(l):
    """done | todo for one product on Specify. Done = nothing left to ask, and nothing refused by the engine."""
    if l.open_count:
        return "todo"
    if l.drawing is not None and l.drawing.status == "failed":
        return "todo"
    return "done"


def _get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def summary(sp):
    """[(label, value)] in plain words, for the "Summary to build" box."""
    m, it = sp.get("model", {}), sp.get("item", {})
    out = [("Size", it.get("product_code", "")), ("Quantity", it.get("qty", ""))]
    if it.get("area"):
        out.append(("Area", it["area"]))
    for path, label in KEYS:
        v = _get(m, path)
        if v is None or isinstance(v, (dict, list)):
            continue
        out.append((label, "Yes" if v is True else "No" if v is False else v))
    out += [("Finish: " + (w.get("use") or "").split(",")[0], w.get("code", "")) for w in sp.get("swatches", []) if w.get("code")]
    return out
