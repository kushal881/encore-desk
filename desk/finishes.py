"""The finishes library: every laminate, fabric, edge band and coating the desk has met, with its colour.
A shade is looked up by name or by its code number. One the library has never seen is given an estimated colour from its
name, saved, and marked indicative; it is never a reason to stop or to ask. Claude replaces the estimate rule later."""
import json, os, re
from sqlalchemy import select
from . import db

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "finishes.json")
WORDS = (("black", "#222224"), ("noir", "#2A2426"), ("charcoal", "#3A3D42"), ("gun grey", "#38414A"), ("dark grey", "#4A4E54"), ("slate", "#6C727A"),
         ("grey", "#8C9096"), ("gray", "#8C9096"), ("silver", "#B8BCC0"), ("frosty", "#F2F2EF"), ("arctic", "#ECECE8"), ("white", "#EEEEEA"),
         ("ivory", "#EFE9D8"), ("cream", "#EDE4CF"), ("beige", "#D8C9A8"), ("maple", "#D9B98A"), ("beech", "#D2A679"), ("teak", "#A9743F"),
         ("cherry", "#8E4A32"), ("mahogany", "#6A2E22"), ("wenge", "#3E2C24"), ("walnut", "#7A5A3E"), ("oak", "#B79A6E"), ("ash", "#C9B48E"),
         ("rust", "#8A3B2A"), ("maroon", "#70303A"), ("red", "#A8322C"), ("orange", "#D1742B"), ("yellow", "#D9B43A"), ("green", "#4E7A52"),
         ("teal", "#2F6F73"), ("navy", "#22324F"), ("blue", "#3B5F91"), ("purple", "#5B4A7A"), ("brown", "#6B4A35"))


def _norm(text):
    return " ".join((text or "").upper().replace(",", " ").split())


def _codes(text):
    return set(re.findall(r"\b[A-Z]?-?\d{3,5}\b", _norm(text)))


def _shade(hex_, factor):
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(v * factor))) for v in (r, g, b))


def estimate(name):
    low = name.lower()
    base = next((h for w, h in WORDS if w in low), "#B5AA98")
    if "dark" in low and not any(w in low for w in ("black", "dark grey")):
        base = _shade(base, 0.72)
    if "light" in low:
        base = _shade(base, 1.12)
    return base


def seed(s):
    if s.scalar(select(db.Finish.id).limit(1)) is None and os.path.exists(SEED):
        for f in json.load(open(SEED, encoding="utf-8")):
            s.add(db.Finish(name=f["name"], kind=f["kind"], hex=f["hex"], source=f["source"]))
        s.commit()


def lookup(s, text, kind):
    """-> the Finish for this shade text, adding it with an estimated colour if the library has never seen it."""
    name = _norm(text)
    if not name:
        return None
    f = s.scalar(select(db.Finish).where(db.Finish.name == name))
    if f is None:
        codes = _codes(name)
        if codes:
            f = next((x for x in s.scalars(select(db.Finish)) if codes & _codes(x.name) and (x.kind == kind or kind == "")), None)
    if f is None:
        f = db.Finish(name=name, kind=kind, hex=estimate(name), source="estimate")
        s.add(f); s.commit()
    return f


def rgb(hex_):
    return [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]
