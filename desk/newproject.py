"""Screen 2: New project and Remake.
- From an Odoo quotation: only quotations whose opportunity is still open (not won, not lost) and that are not on the desk
  yet, plus the server folder number. The folder name is shown before the project is made.
- Remake from a past order: pick client-approved drawings; their approved specification is copied into a project for an
  open quotation (a matching product of that quotation is used, otherwise the product is added and the salesperson is told
  to update the quotation). The copy is drawn at once and goes to review like any preview.
Routes are kept here, apart from app.py; app.py includes this router at its end."""
import datetime, re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select
from . import db
from .app import T, User, ctx, load, ODOO, SOURCE, product_key, _next_no, _new_line, _draw_lines, stage_of
from .reading import read_line
from .sources.sample import SampleSource
from .sources.odoo import OdooError

router = APIRouter()


def folder_name(client, folder_no, when=None):
    """The client's folder on the server, as the drawing numbers use it: Encore-Nov-2026-05-Fault Code Design."""
    when = when or datetime.date.today()
    return "Encore-%s-%d-%s-%s" % (when.strftime("%b"), when.year, folder_no, " ".join(re.sub(r'[\\/:*?"<>|]+', " ", client).split()))


def clean_folder_no(v):
    """'5' / '05' -> '05'; anything else -> None. Folder numbers run 1 to 99."""
    v = (v or "").strip()
    return "%02d" % int(v) if v.isdigit() and 0 < int(v) < 100 else None


def open_quotations(s, q=""):
    """Quotations that can start a project: Odoo's open ones (or the sample orders without Odoo), not yet on the desk."""
    known = {o.source_ref for o in s.scalars(select(db.Order))}
    if ODOO.configured:
        rows = ODOO.open_quotations(limit=60)
    else:
        rows = [dict(ref=r, client="", stage="Sample order") for r in SampleSource().list_refs()]
    q = q.strip().lower()
    return [r for r in rows if r["ref"] not in known and (not q or q in r["ref"].lower() or q in r["client"].lower())]


def folder_in_use(s, folder_no, client_name, when=None):
    """Another client already holds this folder number this month: the drawing numbers would clash. -> that client or ''."""
    when = when or datetime.date.today()
    for c in s.scalars(select(db.Client).where(db.Client.folder_no == folder_no, db.Client.name != client_name)):
        for o in s.scalars(select(db.Order).where(db.Order.client_id == c.id)):
            d = o.read_at
            if d and (d.year, d.month) == (when.year, when.month):
                return c.name
    return ""


def approved_drawings(s, q=""):
    """Client-approved drawings that can be made again: the latest revision of each drawing number, approved on the Issue
    screen (an approval record) or in an order whose approval was recorded as a step (projects from before approval records)."""
    latest = {}
    for r in s.scalars(select(db.Revision).order_by(db.Revision.id)):
        latest[r.number] = r
    out = []
    for r in latest.values():
        line = s.get(db.OrderLine, r.line_id)
        o = s.get(db.Order, line.order_id) if line else None
        if o is None:
            continue
        ap = s.scalars(select(db.Approval).where(db.Approval.revision_id == r.id).order_by(db.Approval.id.desc())).first()
        st, _ = stage_of(o.steps, True)
        ok = (ap.outcome == "approved") if ap else st in ("approved", "production", "completed")
        if not ok:
            continue
        item = r.spec.get("item", {})
        name = item.get("product_name") or line.name.split("(")[0].strip()
        text = " ".join((name, item.get("product_code", ""), r.number, o.client.name, o.source_ref)).lower()
        if q and q.strip().lower() not in text:
            continue
        ph = s.scalar(select(db.Photo).where(db.Photo.product == product_key(line.name)))
        out.append(dict(id=r.id, name=name, size=item.get("product_code", ""), number=r.number, rev=r.rev, client=o.client.name, ref=o.source_ref,
                        photo=ph.id if ph else None, line_name=line.name, description=line.description, qty=line.qty))
    return sorted(out, key=lambda d: d["number"], reverse=True)


# ---- issue #86: results as you type, select a card, lines counted ------------------------------------------------------

MIN_LETTERS = 2
ODOO_DOWN = "Odoo could not be reached just now. Projects already on the desk are still shown. Try again in a minute."


def search(q, limit=12):
    """Quotations and orders matching q (number or client), for New project and the dashboard search box.
    -> (results, note). Under 2 letters: nothing is searched. Each result says whether it is already on the desk."""
    q = (q or "").strip()
    if len(q) < MIN_LETTERS:
        return [], ""
    note, rows = "", []
    if ODOO.configured:
        try:
            rows = ODOO.search_orders(q, limit)
        except OdooError:
            note = ODOO_DOWN
    else:
        for ref in SampleSource().list_refs():
            src = SampleSource().get_order(ref)
            if q.lower() in ref.lower() or q.lower() in src.client_name.lower():
                rows.append(dict(ref=ref, client=src.client_name, kind="Sample order", date=src.date or "", amount=sum((l.price or 0) * (l.qty or 0) for l in src.lines),
                                 currency="", lines=len(src.lines), salesperson=src.salesperson))
    with db.Session() as s:
        orders = {o.source_ref: o for o in s.scalars(select(db.Order))}
        for r in rows:
            o = orders.get(r["ref"])
            r["on_desk"] = bool(o is not None and not o.archived_at)
        seen = {r["ref"] for r in rows}
        for o in sorted(orders.values(), key=lambda x: -x.id):                # projects already on the desk (works without Odoo)
            if o.archived_at or o.source_ref in seen or not (q.lower() in o.source_ref.lower() or q.lower() in o.client.name.lower()):
                continue
            rows.append(dict(ref=o.source_ref, client=o.client.name, kind="On the desk", date=o.read_at.strftime("%d %b %Y").upper() if o.read_at else "",
                             amount=0, currency="", lines=len(o.lines) + len(o.not_drawn), salesperson=o.salesperson, on_desk=True))
    return rows[:limit], note


@router.get("/new/search")
def search_json(q: str = "", user=User):
    rows, note = search(q)
    return JSONResponse(dict(q=q, results=rows, note=note, min=MIN_LETTERS))


def line_counts(ref):
    """The quotation's lines counted, not listed (issue #86): drawn / bought in / set aside, and drawn per area.
    -> dict(drawn, bought, aside: [(no, name, qty, why)], areas: [(area, n)]) or None when it cannot be read."""
    try:
        src = SOURCE.get_order(ref)
    except KeyError:
        return None
    except Exception:
        return dict(error=ODOO_DOWN)
    drawn, bought, aside, areas = [], [], [], {}
    for l in src.lines:
        row = (l.no, l.name.split("(")[0].strip(), l.qty, l.no_drawing)
        if not l.no_drawing:
            drawn.append(row)
            a = (read_line(l.name, l.description).get("area") or "").strip() or "No area"
            areas[a] = areas.get(a, 0) + 1
        elif l.no_drawing.startswith("Bought"):
            bought.append(row)
        else:
            aside.append(row)                                          # services, installation, delivery, freight (hard rule 6)
    return dict(ref=src.ref, client=src.client_name, salesperson=src.salesperson, drawn=drawn, bought=bought, aside=aside,
                areas=sorted(areas.items(), key=lambda x: (x[0] == "No area", -x[1], x[0])))


def designers():
    with db.Session() as s:
        return [u.name for u in s.scalars(select(db.User).order_by(db.User.name)) if getattr(u, "active", True) and u.role in ("drawing", "admin", "admin2")]


@router.get("/new/pick", response_class=HTMLResponse)
def pick(request: Request, ref: str = "", user=User):
    """The green "Selected" card for one quotation (fetched by the page when a result is chosen)."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref.strip()))
        on_desk = o is not None and not o.archived_at
    return T.TemplateResponse(request, "_newpick.html", dict(ref=ref.strip(), on_desk=on_desk, c=None if on_desk else line_counts(ref.strip())))


@router.get("/new", response_class=HTMLResponse)
def new_page(request: Request, q: str = "", p: str = "", ref: str = "", error: str = "", user=User):
    with db.Session() as s:
        quotes = open_quotations(s, "")
        past = approved_drawings(s, p)
        on_desk = [o.source_ref for o in s.scalars(select(db.Order).order_by(db.Order.id.desc())) if not o.archived_at and (not o.lines or not any(l.revisions for l in o.lines))]
        sel_on_desk = bool(ref) and s.scalar(select(db.Order).where(db.Order.source_ref == ref.strip())) is not None
    results, note = search(q) if q else ([], "")
    return T.TemplateResponse(request, "newproject.html", ctx(user, quotes=quotes, past=past, q=q, p=p, error=error, on_desk=on_desk, results=results, note=note,
                                                               sel=ref.strip(), sel_on_desk=sel_on_desk, counts=line_counts(ref.strip()) if ref and not sel_on_desk else None,
                                                               designers=designers(), odoo_on=ODOO.configured, today=datetime.date.today(), nav="new"))


def _start(ref, folder_no, who):
    """Reads the quotation into a new project (or opens the project it already is) and keeps the folder number on the client.
    -> the order, or raises HTTPException with the reason in plain words."""
    ref = (ref or "").strip()
    if not ref:
        raise HTTPException(400, "Pick a quotation first")
    fno = clean_folder_no(folder_no)
    with db.Session() as s:
        existing = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        if existing is None:
            try:
                src = SOURCE.get_order(ref)
            except KeyError:
                raise HTTPException(404, "Quotation %s was not found" % ref)
            except Exception as e:
                raise HTTPException(502, "Odoo could not be read: %s" % str(e)[:200])
            c = s.scalar(select(db.Client).where(db.Client.name == src.client_name))
            if c is not None and c.folder_no:                             # a client the desk knows keeps its folder number
                if (folder_no or "").strip() and fno != c.folder_no:
                    raise HTTPException(400, "%s already has folder %s on the server. Use %s, or ask Kushal to change it." % (c.name, c.folder_no, c.folder_no))
                fno = c.folder_no
            if fno is None:
                raise HTTPException(400, "Enter the server folder number (1 to 99) for this client")
            other = folder_in_use(s, fno, src.client_name)
            if other:
                raise HTTPException(400, "Folder %s is already used this month by %s. Pick another number." % (fno, other))
    o = load(ref)                                                         # reads the products, questions and photographs
    with db.Session() as s:
        c = s.get(db.Client, o.client.id)
        if fno and not c.folder_no:
            c.folder_no = fno
            s.add(db.Message(order_id=o.id, who="desk", text="Project started by %s. Server folder: %s." % (who, folder_name(c.name, fno))))
        s.commit()
    return load(ref)


@router.post("/new")
def new_project(ref: str = Form(""), folder_no: str = Form(""), notes: str = Form(""), needed_by: str = Form(""), designer: str = Form(""), user=User):
    try:
        o = _start(ref, folder_no, user.name)
    except HTTPException as e:
        return RedirectResponse("/new?error=%s%s" % (_q(e.detail), "&ref=" + _q(ref.strip()) if ref.strip() else ""), status_code=303)
    notes, needed_by, designer = notes.strip()[:2000], needed_by.strip()[:40], designer.strip()[:120]
    if notes or needed_by or designer:                                   # issue #86: the brief for the designer
        with db.Session() as s:
            od = s.get(db.Order, o.id)
            od.brief, od.needed_by, od.designer = notes, needed_by, designer
            s.add(db.Message(order_id=o.id, who="desk", text="Brief for the designer from %s:%s%s%s" % (
                user.name, (" designer %s." % designer) if designer else "", (" Needed by %s." % needed_by) if needed_by else "", (" " + notes) if notes else "")))
            s.commit()
    return RedirectResponse("/orders/%s" % o.source_ref, status_code=303)


def _q(t):
    from urllib.parse import quote
    return quote(str(t))


@router.post("/new/remake")
async def remake_project(request: Request, user=User):
    """Copies the approved specification of the picked drawings into the project of an open quotation."""
    form = await request.form()
    rids = [int(x) for x in form.getlist("rid") if str(x).isdigit()]
    ref, fno = (form.get("ref") or "").strip(), form.get("folder_no") or ""
    if not rids:
        return RedirectResponse("/new?error=%s#remake" % _q("Tick at least one past drawing to make again"), status_code=303)
    with db.Session() as s:
        if not any(r in {d["id"] for d in approved_drawings(s)} for r in rids):
            return RedirectResponse("/new?error=%s#remake" % _q("Only client-approved drawings can be made again"), status_code=303)
    try:
        o = _start(ref, fno, user.name)
    except HTTPException as e:
        return RedirectResponse("/new?error=%s#remake" % _q(e.detail), status_code=303)
    done = []
    with db.Session() as s:
        allowed = {d["id"]: d for d in approved_drawings(s)}
        used = set()
        for rid in rids:
            d = allowed.get(rid)
            if d is None:
                continue                                                  # not client-approved (or gone): never copied
            r = s.get(db.Revision, rid)
            # a product of the quotation with the same name, not issued and not already a remake, takes the copy
            line = next((l for l in o.lines if l.no not in used and not l.revisions and not l.remake
                         and product_key(l.name) == product_key(d["line_name"])), None)
            if line is not None:
                no, lid = line.no, line.id
            else:
                no = _next_no(s, o.id)
                nl = _new_line(s, o.id, no, d["line_name"], d["description"], d["qty"])
                s.flush()
                lid = nl.id
                s.add(db.AddedLine(order_id=o.id, no=no, name=d["line_name"], by=user.name))
            for c in s.scalars(select(db.Change).where(db.Change.line_id == lid)):
                if "remake_of" in (c.values or {}):
                    c.status = "rejected"
            s.add(db.Change(line_id=lid, typed="Product %d: make it the same as %s Rev %s" % (no, r.number, r.rev),
                            understood="exact remake of %s Rev %s" % (r.number, r.rev), values={"remake_of": rid}, status="confirmed", by=user.name))
            used.add(no); done.append((no, r.number, r.rev, line is None))
        if done:
            s.add(db.Message(order_id=o.id, who="desk", text="Remake by %s: %s." % (user.name, "; ".join(
                "Product %d is %s Rev %s%s" % (n, num, rv, " (added: not in the quotation, the salesperson must add it)" if added else "") for n, num, rv, added in done))))
        s.commit()
    if not done:
        return RedirectResponse("/new?error=%s#remake" % _q("Only client-approved drawings can be made again"), status_code=303)
    o = load(ref)
    with db.Session() as s:
        _draw_lines(o, s, user.name, only={n for n, *_ in done})
    return RedirectResponse("/orders/%s" % ref, status_code=303)
