"""Products tab (issue #84): one tab for every product of an order instead of a tab per drawing, built for 100+ products.
Search by name, number or area; filter chips with counts (Your review, AI waiting, Bridge, Ready to issue, No drawing);
grouped by area in folding groups ("25 products · 9 ready"); columns #, product, area, qty, drawn by, status; tick boxes and
a pinned bulk bar ("40 selected · Issue 40 to client", "Open first for review"). Approved or locked products are shown but
cannot be ticked. Bought-in and set-aside lines show "No drawing". Wording: Product 1, 2 ... (hard rule 5).
Routes are kept here, apart from app.py; app.py includes this router at its end."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from .app import T, User, ctx, load, _ready, _locked

router = APIRouter()
NO_AREA = "No area"


def status_of(o, l):
    """-> (key, text, css class). key: approved | changes | issued | ai | bridge | ready | review | failed | questions | notdrawn | cancelled"""
    if l.cancelled_at:
        return "cancelled", "Cancelled", "dim"
    ns = getattr(l, "nav_status", "")
    if ns == "Approved" or (l.last_rev and _locked(o, l)):
        return "approved", "Approved", "ok"
    if ns == "Changes asked":
        return "changes", "Changes asked", "ask"
    if getattr(l, "ai_waiting", False):
        return "ai", "AI change waiting", "info"
    if l.issued_current:
        return "issued", "Issued Rev %s" % l.last_rev.rev, "info"
    if l.drawing is not None and l.drawing.status == "failed":
        return "failed", "Could not draw", "bad"
    if l.open_count:
        return "questions", "%d question%s" % (l.open_count, "" if l.open_count == 1 else "s"), "ask"
    if l.drawing is None:
        return "notdrawn", "Not drawn yet", "dim"
    if _ready(l):
        return ("ready", "Ready to issue", "ok") if (getattr(l, "review", None) or getattr(l, "bridge", False)) else ("review", "Your review", "ask")
    return "review", "Your review", "ask"


def rows_for(o):
    """Every product and every no-drawing line of an order, for the tab and its tests."""
    out = []
    for l in sorted(o.lines, key=lambda x: x.no):
        key, text, cls = status_of(o, l)
        areas = [a.area for a in getattr(l, "areas", [])] or [NO_AREA]
        out.append(dict(no=l.no, name=l.name.split("(")[0].strip(), area=areas[0], areas=", ".join(areas), qty=l.qty,
                        by="Bridge" if getattr(l, "bridge", False) else ("Engine" if l.drawing is not None or l.revisions else "—"),
                        key=key, text=text, cls=cls, bridge=bool(getattr(l, "bridge", False)), url="/orders/%s/view/%d" % (o.source_ref, l.no) if (l.drawing or l.revisions) else "/orders/%s/specify/%d" % (o.source_ref, l.no),
                        pick=key not in ("approved", "cancelled"),                  # approved / locked: shown, never ticked
                        issuable=key == "ready"))
    for x in sorted(o.not_drawn, key=lambda x: x.no or 0):
        out.append(dict(no=x.no or 0, name=x.name, area=NO_AREA, areas="", qty=x.qty, by="—", key="nodrawing", text="No drawing · " + (x.reason or "set aside").lower(),
                        cls="dim", bridge=False, url="/orders/%s#notdrawn" % o.source_ref, pick=False, issuable=False))
    return out


def groups_of(rows):
    """[(area, rows, ready count)] in the order areas first appear; "No area" last."""
    seen = {}
    for r in rows:
        seen.setdefault(r["area"], []).append(r)
    areas = [a for a in seen if a != NO_AREA] + ([NO_AREA] if NO_AREA in seen else [])
    return [(a, seen[a], sum(1 for r in seen[a] if r["key"] == "ready")) for a in areas]


CHIPS = (("all", "All"), ("review", "Your review"), ("ai", "AI waiting"), ("bridge", "Bridge"), ("ready", "Ready to issue"), ("nodrawing", "No drawing"))


def counts(rows):
    c = {k: 0 for k, _ in CHIPS}
    c["all"] = len(rows)
    for r in rows:
        if r["key"] in c:
            c[r["key"]] += 1
        if r["bridge"] and r["key"] != "bridge":
            c["bridge"] += 1
    return c


@router.get("/orders/{ref}/products", response_class=HTMLResponse)
def products(request: Request, ref: str, user=User):
    o = load(ref)
    rows = rows_for(o)
    return T.TemplateResponse(request, "products.html", ctx(user, o=o, tab="products", rows=rows, groups=groups_of(rows), counts=counts(rows), chips=CHIPS,
                                                             folder_no=o.client.folder_no or ""))
