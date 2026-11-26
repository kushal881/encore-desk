"""Turns a typed change request into values for the specification, and says back how it was understood.
Rule-based for now, for the common requests. Anything it cannot read is refused plainly, never guessed;
Claude takes over this reading later, behind the same function."""
import re

FAMILY_WORDS = {"workstation": ("workstation", "table", "desk"), "storage": ("storage", "cabinet"), "pedestal": ("pedestal",)}


def which_line(text, lines):
    """lines = [(no, family)]. -> line no, or None if the text does not say and the order has more than one line."""
    low = text.lower()
    m = re.search(r"\b(?:product|line|item|sr\.?\s*no\.?)\s*(\d+)", low)
    if m and int(m.group(1)) in [n for n, _ in lines]:
        return int(m.group(1))
    hits = [n for n, fam in lines if any(w in low for w in FAMILY_WORDS.get(fam, ()))]
    if len(set(hits)) == 1:
        return hits[0]
    return lines[0][0] if len(lines) == 1 else None


def understand(text, family):
    """-> (values, summary) for one line, or (None, why not)."""
    low = re.sub(r"\b(?:product|line|item)\s*\d+\b", " ", text.lower())
    v, said = {}, []
    for key, words in (("L", "long|length|wide|width"), ("D", "deep|depth"), ("H", "high|height|tall")):
        m = re.search(r"\b%s\s*(\d{3,4})\b" % key.lower(), low) or re.search(r"(\d{3,4})\s*(?:mm)?\s*(?:%s)\b" % words, low) \
            or re.search(r"(?:%s)\s*(?:to|of|=|:)?\s*(\d{3,4})" % words, low)
        if m and not re.search(r"(screen|raceway)[^.]{0,25}%s" % re.escape(m.group(0)), low):
            v[key] = int(m.group(1)); said.append("%s %d mm" % ({"L": "length", "D": "depth", "H": "height"}[key], v[key]))
    m = re.search(r"hing\w*\s*(?:on\s*(?:the\s*)?|to\s*(?:the\s*)?|side\s*)?(left|right)|(left|right)\s*(?:hand\s*)?hing", low)
    if m and family == "pedestal":
        v["hinge"] = m.group(1) or m.group(2); said.append("shutter hinged on the %s" % v["hinge"])
    m = re.search(r"(\d+)\s*shel(?:f|ves)|shel(?:f|ves)\s*(?:to|=|:)?\s*(\d+)|\bno\s+shel(?:f|ves)", low)
    if m and family == "storage":
        v["shelves"] = int(m.group(1) or m.group(2) or 0); said.append("%d shel%s in each compartment" % (v["shelves"], "f" if v["shelves"] == 1 else "ves"))
    m = re.search(r"(\d+)\s*shutters?|shutters?\s*(?:to|=|:)?\s*(\d+)", low)
    if m and family == "storage":
        v["shutters"] = int(m.group(1) or m.group(2)); said.append("%d shutters" % v["shutters"])
    m = re.search(r"screen[^.\d]{0,20}(\d{3,4})(?:\s*(?:x|by)\s*(\d{3}))?", low)
    if m and family == "workstation":
        v["screen_w"] = int(m.group(1)); said.append("screen %d wide" % v["screen_w"])
        if m.group(2):
            v["screen_h"] = int(m.group(2)); said[-1] = "screen %d x %d" % (v["screen_w"], v["screen_h"])
    m = re.search(r"raceway[^.\d]{0,20}(\d{3,4})", low)
    if m and family == "workstation":
        v["raceway_L"] = int(m.group(1)); said.append("raceway %d long" % v["raceway_L"])
    if not v:
        return None, "I could not turn this into a change the engine can draw. If it is a new kind of detail, it needs an engine addition; you can ask for one below."
    return v, "; ".join(said)


def compact(values):
    """A short form of a change for the revision strip on the sheet, which holds about 40 characters."""
    names = dict(L="L", D="D", H="H", hinge="hinge", shelves="shelves", shutters="shutters", screen_w="screen W", screen_h="screen H", raceway_L="raceway", use_engine="engines")
    return ", ".join("%s %s" % (names.get(k, k), v) for k, v in values.items())
