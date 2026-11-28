"""Screen 2: New project and Remake.
- From an Odoo quotation: only quotations whose opportunity is still open (not won, not lost) and that are not on the desk
  yet, plus the server folder number. The folder name is shown before the project is made.
- Remake from a past order: pick client-approved drawings; their approved specification is copied into a project for an
  open quotation (a matching product of that quotation is used, otherwise the product is added and the salesperson is told
  to update the quotation). The copy is drawn at once and goes to review like any preview.
Routes are kept here, apart from app.py; app.py includes this router at its end."""
import datetime, re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db
from .app import T, User, ctx, load, ODOO, SOURCE, product_key, _next_no, _new_line, _draw_lines, stage_of
from .sources.sample import SampleSource

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


@router.get("/new", response_class=HTMLResponse)
def new_page(request: Request, q: str = "", p: str = "", error: str = "", user=User):
    with db.Session() as s:
        quotes = open_quotations(s, q)
        past = approved_drawings(s, p)
        on_desk = [o.source_ref for o in s.scalars(select(db.Order).order_by(db.Order.id.desc())) if not o.archived_at and (not o.lines or not any(l.revisions for l in o.lines))]
    return T.TemplateResponse(request, "newproject.html", ctx(user, quotes=quotes, past=past, q=q, p=p, error=error, on_desk=on_desk,
                                                               odoo_on=ODOO.configured, today=datetime.date.today(), nav="new"))


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
def new_project(ref: str = Form(""), folder_no: str = Form(""), user=User):
    try:
        o = _start(ref, folder_no, user.name)
    except HTTPException as e:
        return RedirectResponse("/new?error=%s" % _q(e.detail), status_code=303)
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
