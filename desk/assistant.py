"""The AI change assistant of the drawing viewer. It reads what the user typed (with the markers on the drawing) and
answers in one of three ways: values to change in the product file, an answer to a question, or "cannot".

Hard rules (CLAUDE.md): the assistant never draws or edits a PDF. It only proposes values for the product file's
"model"; the desk checks every value here, and the locked engine draws. The engine's own checks decide whether the
result can be accepted.

Claude is used when ANTHROPIC_API_KEY is set. Without it the desk's own fixed rules (changes.understand) stand in, so
the panel works for the common size / shelf / hinge / screen changes and says plainly that questions need the key."""
import copy, json, os, re

from . import changes as chg

MODEL = os.environ.get("ASSISTANT_MODEL", "claude-opus-5-5")
OPTIONAL = {"interior", "raceway"}            # engine options that may be added to a product file (see the drawing rules)
LEGACY = {"model.L": "L", "model.D": "D", "model.H": "H"}   # the size is kept with the order's other values

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["change", "answer", "cannot"]},
        "said": {"type": "string", "description": "One short line in plain words: what changes, e.g. 'Screen width 900 -> 800'. Empty for an answer."},
        "changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Dotted path inside the product file, always starting with model., e.g. model.screen.w"},
                           "value": {"type": "string", "description": "The new value as JSON, e.g. 800, true, \"left\" or {\"partitions\": 1}"}},
            "required": ["path", "value"], "additionalProperties": False}},
        "answer": {"type": "string", "description": "For kind answer or cannot: the reply to the user, plain and short."},
    },
    "required": ["kind", "said", "changes", "answer"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You are the change assistant of the Encore Prime Furniture drawing desk. Staff and designers use you while
reviewing a product's drawing. You never draw and never edit a PDF: you only propose new values for the product file's
"model" section, and the locked drawing engine redraws from them and runs its checks (no clashes, true-view isometric,
approved engines). If the engine cannot build a value, it refuses and the user sees why.

How to answer:
- kind "change": the user wants something on this product changed and it can be expressed with the values in its
  "model" (shown to you), or with an optional engine key the rules below describe (interior, raceway). Give each value
  as a dotted path starting with model. and the new value as JSON. Change only what was asked. Keep "said" to one line,
  with old -> new values where you know them.
- kind "answer": the user asked a question. Answer from the rules and product knowledge below and from the product's
  values. Be short and plain; the reader is not an engineer. If the answer suggests a change, say what you would change
  and let them ask for it.
- kind "cannot": the request cannot be expressed with the product's values or the engine options in the rules (a new
  kind of part, a different product type, anything about the PDF itself). Say that it needs an engine change, which
  Kushal approves, and name what would be needed. Never invent a key the product file does not have.
Markers: the user may refer to numbered markers ("at 1"). Each comes with the part it sits on and its note; use them
to know which part is meant. A change for one client never changes the engine or the rules: it is this product only.
Wording: products are "Product 1, 2...", never "Line 1". Sizes are in mm.

ENCORE DRAWING RULES, PRODUCT RULES AND PRODUCT KNOWLEDGE (read-only; for answers and to keep changes within the rules):
"""


def configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _rules(engines_dir):
    """The engines' standing rules and product knowledge, read-only, in a fixed order (keeps the cached prefix stable)."""
    out = []
    for name in ("ENCORE_DRAWING_RULES.txt", "ENCORE_RULES_PRODUCTS.txt", "ENCORE_PRODUCT_KNOWLEDGE.txt"):
        p = os.path.join(engines_dir or "", name)
        if os.path.exists(p):
            out.append("=== %s ===\n%s" % (name, open(p, encoding="utf-8").read()))
    return "\n\n".join(out)


def _call(system, user):
    """One request to Claude. -> the parsed JSON reply, or dict(kind=cannot) with the reason."""
    import anthropic
    client = anthropic.Anthropic()
    try:
        r = client.beta.messages.create(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",          # a declined request is retried on another model
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],   # the rules are the same every time: cached
            messages=[{"role": "user", "content": user}])
    except anthropic.RateLimitError:
        return dict(kind="cannot", said="", changes=[], answer="The assistant is busy. Try again in a minute.")
    except anthropic.AuthenticationError:
        return dict(kind="cannot", said="", changes=[], answer="The assistant's key is not valid. Ask Kushal to check it in Railway.")
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        return dict(kind="cannot", said="", changes=[], answer="The assistant could not be reached (%s). Try again." % type(e).__name__)
    if r.stop_reason == "refusal":
        return dict(kind="cannot", said="", changes=[], answer="The assistant declined this request. Type it differently, or ask Kushal.")
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        return dict(kind="cannot", said="", changes=[], answer="The assistant's reply could not be read. Try again.")


def _leaves(d, prefix="model"):
    out = {}
    for k, v in (d or {}).items():
        p = "%s.%s" % (prefix, k)
        if isinstance(v, dict):
            out.update(_leaves(v, p))
        else:
            out[p] = v
    return out


def check(values, model):
    """Keeps the assistant inside the product file. values = {path: value} from the assistant; model = the product's
    current "model". -> (clean values for the specification, list of problems in plain words).
    Allowed: an existing value of the model, of the same type; or one of the engine's optional keys (OPTIONAL)."""
    have, clean, bad = _leaves(model), {}, []
    for path, v in values.items():
        top = path.split(".")[1] if path.count(".") >= 1 else ""
        if not path.startswith("model."):
            bad.append("%s is not part of the product's model" % path)
        elif path in LEGACY:
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 100 <= v <= 6000:
                clean[LEGACY[path]] = int(v)
            else:
                bad.append("%s must be a size in mm" % path[6:])
        elif path in have:
            old = have[path]
            same = (isinstance(old, bool) == isinstance(v, bool)) and (
                isinstance(v, type(old)) or (isinstance(old, (int, float)) and isinstance(v, (int, float))) or old is None)
            if same and not (isinstance(old, list) and len(v) != len(old)):
                clean[path] = v
            else:
                bad.append("%s must stay a %s" % (path[6:], type(old).__name__.replace("str", "word").replace("int", "number").replace("float", "number")))
        elif top in OPTIONAL and path.count(".") == 1:
            clean[path] = v
        else:
            bad.append("%s is not a value this product has" % path[6:])
    return clean, bad


def apply(spec, values):
    """Writes model.* values into a specification (the order's L / D / H are applied by spec.build)."""
    spec = copy.deepcopy(spec)
    for path, v in values.items():
        if not path.startswith("model."):
            continue
        keys, d = path.split(".")[1:], spec["model"]
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = v
    return spec


def ask(text, product, model, markers, engines_dir, history=()):
    """-> dict(kind change | answer | cannot, said, values, answer, problems).
    product = plain description (Product N, name, size, issued state); markers = ["1 = note, on Part (view)"];
    history = earlier turns on this product, [(typed, reply)]."""
    if not configured():
        values, said = chg.understand(text, product.get("family", ""))
        if values:                                    # the desk's own change keys (L, hinge, shelves ...), applied by spec.build
            return dict(kind="change", said=said, values=values, problems=[], answer="")
        if re.search(r"\?\s*$|^(why|what|how|which|can|is|are|does|do)\b", text.strip(), re.I):
            return dict(kind="cannot", said="", values={}, problems=[], answer="Questions need the AI assistant, which is not switched on yet (no API key in Railway). Size, shelf, hinge, screen and raceway changes work without it.")
        return dict(kind="cannot", said="", values={}, problems=[], answer=said)
    user = "\n".join([
        "PRODUCT: %s" % json.dumps(product, sort_keys=True),
        "CURRENT MODEL VALUES (the product file's \"model\"):",
        json.dumps(model, sort_keys=True, indent=1),
        "MARKERS ON THE DRAWING: %s" % ("; ".join(markers) or "none"),
        "EARLIER ON THIS PRODUCT: %s" % ("; ".join("asked %r -> %s" % (t, r) for t, r in history[-6:]) or "nothing"),
        "THE USER TYPED: %s" % text,
    ])
    return _finish(_call(INSTRUCTIONS + _rules(engines_dir), user), model)


def _finish(reply, model):
    kind = reply.get("kind") if reply.get("kind") in ("change", "answer", "cannot") else "cannot"
    out = dict(kind=kind, said=(reply.get("said") or "").strip()[:300], values={}, problems=[], answer=(reply.get("answer") or "").strip()[:2000])
    if kind != "change":
        return out
    raw = {}
    for c in reply.get("changes") or []:
        try:
            raw[str(c["path"]).strip()] = json.loads(c["value"])
        except (KeyError, TypeError, ValueError):
            out["problems"].append("a value could not be read")
    out["values"], bad = check(raw, model)
    out["problems"] += bad
    if not out["values"]:
        out["kind"] = "cannot"
        out["answer"] = out["answer"] or "I could not turn this into a change of this product's values (%s)." % "; ".join(out["problems"] or ["nothing to change"])
    return out
