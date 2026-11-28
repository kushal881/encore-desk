"""Finishes still left as placeholders (issue #45): a drawing must not reach the client with "LAMINATE NAME + CODE" or
"FABRIC NAME + CODE" in its finishes box. The approved templates carry these placeholders; the order line normally
replaces them (Laminate : ..., Fabric : ...). When it does not, the desk asks on Specify and refuses to issue until the
preview shows real finishes. Previews and production previews (not issued) may still show placeholders."""
import re

PLACEHOLDER = re.compile(r"NAME\s*\+\s*CODE|\bTBD\b|\bTBC\b|\[[^\]]*\]", re.I)
QUESTIONS = {
    "laminate": "The quotation line gives no laminate. Which laminate (name and code)?",
    "fabric": "The quotation line gives no fabric. Which fabric for the screen (name and code)?",
}


def kind_of(sw):
    """laminate | fabric | the swatch's own use, for one finish swatch of the product file."""
    t = ("%s %s" % (sw.get("use", ""), sw.get("code", ""))).lower()
    if "fabric" in t:
        return "fabric"
    if any(w in t for w in ("laminate", "plpb", "carcass")):
        return "laminate"
    return (sw.get("use") or "finish").strip().lower()


def is_placeholder(value):
    v = (value or "").strip()
    return not v or bool(PLACEHOLDER.search(v))


def missing(spec):
    """The finishes of a product file that are still placeholders or empty, by kind, in order. [] = all set (or no file)."""
    out = []
    for sw in (spec or {}).get("swatches", []) or []:
        if isinstance(sw, dict) and is_placeholder(sw.get("code")):
            k = kind_of(sw)
            if k not in out:
                out.append(k)
    return out


def questions(spec, fields, have=()):
    """Specify questions for the placeholder finishes a question can fix (laminate, fabric) and the order line does not give."""
    return [dict(key=k, text=QUESTIONS[k], options=[]) for k in missing(spec)
            if k in QUESTIONS and not (fields or {}).get(k) and k not in have]


def reason(spec):
    """'finishes not set (laminate, fabric)' or ''."""
    m = missing(spec)
    return "finishes not set (%s)" % ", ".join(m) if m else ""
