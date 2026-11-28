"""Screen 11: Production. One product's production sheet (no client details; job order and deadline), with tabs for the
production drawing, the bill of material and the cut list, a copy-for-department download, release to production, and an
AI change panel limited to construction.

Construction only, checked by the engine itself: a change is accepted only when the client drawing it gives is exactly the
approved one (the engine redraws the client sheets with the change and the desk compares them, sheet by sheet, with the
approved revision). So frame, supports, joinery, hardware placement, edge band and cut sizes can change; size, finish,
look and quantity cannot (they change the client drawing) and go the revision route (RevB, admin permission, client
re-approval). The engine does all drawing; the desk only compares what it drew.

Before release a change updates the current production drawing (P1). After release it needs an administrator: approving
it makes the next P (P1 -> P2), and production, the administrators and the salesperson get a stop alert.
Routes are kept here, apart from app.py; app.py includes this router."""
import copy, datetime, os
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy import select
from . import db, engines, assistant, viewer
from .app import T, User, ctx, load, IST

router = APIRouter()
DEPTS = [("all", "All departments", None), ("panel", "CNC / panel", "PANEL"), ("metal", "MS fabrication", "METAL"),
         ("assembly", "Assembly", None), ("vendor", "Subcontractor / vendor", None)]
ALLOWED = "frame and supports, joinery, hardware placement, edge band, cut sizes, sheet details"
LOCKED = "Size, finish, look and quantity are locked by the client approval."


def current_set(l, kind="PRODUCTION"):
    """The production (or vendor) set made from the product's latest issued revision, or None."""
    r = l.last_rev
    return next((x for x in reversed(l.sets) if r and x.kind == kind and x.revision_id == r.id and x.files), None)


def base_spec(ps, r):
    """The specification the current production set was drawn from (older sets kept none: the revision's)."""
    return copy.deepcopy(ps.spec) if ps is not None and ps.spec else copy.deepcopy(r.spec)


def pages_same(pdf_a, pdf_b, dpi=40):
    """True when two PDFs look the same, sheet by sheet (rendered and compared pixel for pixel)."""
    import pymupdf
    try:
        with pymupdf.open(pdf_a) as a, pymupdf.open(pdf_b) as b:
            if len(a) != len(b):
                return False
            return all(a[i].get_pixmap(dpi=dpi).samples == b[i].get_pixmap(dpi=dpi).samples for i in range(len(a)))
    except Exception:
        return False


def client_unchanged(r, new_model, key, edir):
    """Draws the client drawing with the changed model and compares it with the approved revision.
    -> (same?, checks of that drawing, error text)."""
    sp = copy.deepcopy(r.spec)
    sp["model"] = copy.deepcopy(new_model)
    sp.pop("production", None)
    sp["drawing_set"] = r.spec.get("drawing_set", "CLIENT")
    try:
        files = engines.draw(sp, key, edir)
    except RuntimeError as e:
        return False, None, str(e)[:400]
    old = (r.files or {}).get("pdf")
    if not old or not os.path.exists(old):
        return False, files.get("checks"), "The approved client drawing file was not found, so the change cannot be checked against it."
    return pages_same(files["pdf"], old), files.get("checks"), ""


def _bom_rows(path):
    """-> {(section, item): quantity} from the product sheet of a one-product bill of material (cut list and what to buy)."""
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = next((w for w in wb.worksheets if w.title not in ("Start Here", "Summary", "Buying Rules") and not w.title.startswith("Kit")), None)
    out, sec = {}, ""
    if ws is None:
        return out
    for row in ws.iter_rows(min_row=1, values_only=True):
        a = row[0] if row else None
        if isinstance(a, str) and a.startswith("1. CUT LIST"):
            sec = "cut"; continue
        if isinstance(a, str) and a.startswith("2. WHAT TO BUY"):
            sec = "buy"; continue
        if not a or a in ("Part", "Item") or not sec or len(row) < 5:
            continue
        if sec == "cut" and isinstance(row[4], (int, float)):
            size = " x ".join(str(int(v)) for v in (row[2], row[3]) if isinstance(v, (int, float)))
            out[("cut", "%s · %s · %s" % (a, row[1], size))] = row[4]
        elif sec == "buy" and isinstance(row[4], (int, float)):
            out[("buy", "%s (%s)" % (a, row[1]))] = row[4]
    return out


def bom_effect(r, before, after, key, edir, client):
    """What the change does to the product's bill of material: [dict(item, before, after)], or [] when nothing changes."""
    try:
        pa = engines.bom({r.number: before}, key + "-before.xlsx", edir=edir, client=client)
        pb = engines.bom({r.number: after}, key + "-after.xlsx", edir=edir, client=client)
        a, b = _bom_rows(pa), _bom_rows(pb)
    except Exception:
        return [dict(item="Bill of material could not be compared", before="", after="")]
    out = []
    for k in sorted(set(a) | set(b), key=lambda k: (k[0] != "buy", k[1])):
        if a.get(k) != b.get(k):
            fmt = lambda v: "" if v is None else ("%g" % v if isinstance(v, float) else str(v))
            out.append(dict(item=k[1], before=fmt(a.get(k)) or "none", after=fmt(b.get(k)) or "none"))
    return out


def _page(request, user, o, l, tab, sheet, error=""):
    r, ps = l.last_rev, current_set(l)
    vs = current_set(l, "VENDOR")
    with db.Session() as s:
        turns = s.scalars(select(db.ProdChange).where(db.ProdChange.line_id == l.id).order_by(db.ProdChange.id)).all()
        bom = s.scalars(select(db.ProductionFile).where(db.ProductionFile.order_id == o.id, db.ProductionFile.error == "").order_by(db.ProductionFile.id.desc())).first()
    rows = None
    if tab in ("bom", "cut") and bom is not None and os.path.exists(bom.path):
        rows = _order_bom_rows(bom.path, l)
    trial = next((t for t in reversed(turns) if t.status == "trial"), None)
    asked = next((t for t in reversed(turns) if t.status == "asked"), None)
    f0 = ps.files[0] if ps else None
    pages = f0["pages"] if f0 else 0
    sheet = min(max(sheet, 1), max(pages, 1))
    approved = _client_approved(o, l)
    jo = (ps.details.get("job_order") if ps else o.job_order) or ""
    dl = (ps.details.get("deadline") if ps else o.delivery_date) or ""
    return T.TemplateResponse(request, "production.html", ctx(
        user, o=o, l=l, r=r, ps=ps, vs=vs, tab=tab, sheet=sheet, pages=pages, turns=turns, trial=trial, asked=asked,
        bom=bom, rows=rows, depts=DEPTS, allowed=ALLOWED, locked_text=LOCKED, approved=approved, error=error, step=viewer.STEPS,
        job_text=header_value(jo, "Job no. not set"), deadline_text=header_value(dl, "Deadline not set", "deadline "),
        ready=bool(ps), can_release=bool(ps and not ps.released_at and not trial and not asked), nav="projects"))


PLACEHOLDERS = {"", "TBC", "JO-[NO.]", "[DATE]", "[NO.]"}


def header_value(v, missing, prefix=""):
    """The job order or deadline for the page header: the value, or plain words when it is not known yet (issue #33).
    Placeholders the sheets use (JO-[NO.], [DATE], TBC) never show in the header."""
    v = (v or "").strip()
    return missing if v.upper() in PLACEHOLDERS else prefix + v


def _client_approved(o, l):
    from .app import _approval_of
    ap = _approval_of(l.last_rev) if l.last_rev else None
    if ap is not None and ap.outcome == "approved":
        return "Rev%s approved by %s" % (l.last_rev.rev, ap.name)
    if o.stage in ("approved", "production", "completed") and not o.approval_pending:
        return "Rev%s approved" % l.last_rev.rev
    return ""


def _order_bom_rows(path, l):
    """The product's rows in the order's bill of material: its own sheet (NN Name). -> dict(cut=[...], buy=[...])."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path)
    except Exception:
        return None
    ws = next((w for w in wb.worksheets if w.title.startswith("%02d " % l.no)), None)
    if ws is None:                                                       # numbered by serial in older files: by name
        name = l.name.split("(")[0].strip()[:20].lower()
        ws = next((w for w in wb.worksheets if name and name in w.title.lower()), None)
    if ws is None:
        return None
    out, sec = dict(cut=[], buy=[], head=[]), ""
    for row in ws.iter_rows(min_row=1, values_only=True):
        a = row[0] if row else None
        if isinstance(a, str) and a.startswith("1. CUT LIST"):
            sec = "cut"; continue
        if isinstance(a, str) and a.startswith("2. WHAT TO BUY"):
            sec = "buy"; continue
        if a in ("Part", "Item") and sec:
            out["head_" + sec] = [x for x in row if x is not None]; continue
        if sec and a and not (isinstance(a, str) and a.startswith(("Yellow", "="))):
            out[sec].append(["" if v is None else ("…" if isinstance(v, str) and v.startswith("=") else v) for v in row[:len(out.get("head_" + sec, row))]])
    return out


def _line(ref, no):
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not l.last_rev:
        raise HTTPException(404, "Product %d has no issued drawing" % no)
    return o, l


@router.get("/orders/{ref}/production/{no}", response_class=HTMLResponse)
def production_page(request: Request, ref: str, no: int, tab: str = "drawing", s: int = 1, error: str = "", user=User):
    o, l = _line(ref, no)
    return _page(request, user, o, l, tab if tab in ("drawing", "bom", "cut") else "drawing", s, error)


def _back(ref, no, tab="drawing", error=""):
    from urllib.parse import quote
    return RedirectResponse("/orders/%s/production/%d?tab=%s%s#panel" % (ref, no, tab, "&error=" + quote(error) if error else ""), status_code=303)


@router.post("/orders/{ref}/production/{no}/ai")
def production_ai(ref: str, no: int, text: str = Form(""), marker: int = Form(0), user=User):
    """Asks for a construction change. The engine redraws; the change is offered only when the client drawing is unchanged."""
    o, l = _line(ref, no)
    text = " ".join(text.split())[:1000]
    if not text:
        return _back(ref, no, error="Type the change first")
    ps, r = current_set(l), l.last_rev
    if ps is None:
        return _back(ref, no, error="Make the production drawings first")
    with db.Session() as s:
        if s.scalar(select(db.ProdChange.id).where(db.ProdChange.line_id == l.id, db.ProdChange.status.in_(("trial", "asked"))).limit(1)):
            return _back(ref, no, error="A change is already waiting: accept or undo it first")
    base = base_spec(ps, r)
    edir, ver = engines.dir_for(r.engine_version)
    reply = assistant.ask(text, dict(product="Product %d: %s" % (l.no, l.name), family=l.read.get("family", "") if getattr(l, "read", None) else "",
                                     issued="Rev%s, client approved; production drawing P%d" % (r.rev, ps.prod_no), only="construction changes on the production drawing"),
                          base.get("model", {}), ["%d" % marker] if marker else [], edir)
    pc = db.ProdChange(line_id=l.id, set_id=ps.id, marker=marker, typed=text, said=reply.get("said", ""), answer=reply.get("answer", ""), by=user.name)
    if reply["kind"] != "change":
        pc.status = reply["kind"]
    elif any(not k.startswith("model.") for k in reply["values"]):           # the desk's own size / shelf / hinge keys: design
        pc.status, pc.reason = "refused", "That changes the approved design (%s). It can't be done on the production drawing." % (reply.get("said") or "size")
    else:
        pc.values = reply["values"]
        new = assistant.apply(base, reply["values"])
        key = "%d/line%d/prodchange/%s" % (o.id, l.no, datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"))
        same, checks, err = client_unchanged(r, new["model"], key + "/client", edir)
        if err:
            pc.status, pc.reason = "failed", err
        elif not same:
            pc.status, pc.reason = "refused", "That changes the approved design: the client drawing would not be the one the client approved. It can't be done on the production drawing."
        elif checks is not None and not viewer.all_pass(checks):
            pc.status, pc.reason = "failed", "The engine's checks did not pass with this change (clash or isometric check). Keep the drawing as it is, or change it differently."
        else:
            try:
                pc.files = engines.draw_set(new, key + "/set", edir)
                pc.spec, pc.status = new, "trial"
                pc.bom = bom_effect(r, base, new, key + "/bom", edir, o.client.name)
            except RuntimeError as e:
                pc.status, pc.reason = "failed", str(e)[:400]
    with db.Session() as s:
        s.add(pc); s.commit()
    return _back(ref, no)


@router.post("/orders/{ref}/production/{no}/ai/{cid}/{what}")
def production_ai_decide(ref: str, no: int, cid: int, what: str, user=User):
    """accept: before release the change replaces the current production drawing; after release it goes to an
    administrator (P1 -> P2). undo: drops the proposal."""
    o, l = _line(ref, no)
    with db.Session() as s:
        pc = s.get(db.ProdChange, cid)
        if pc is None or pc.line_id != l.id or what not in ("accept", "undo") or pc.status != "trial":
            raise HTTPException(400, "This change cannot be taken now")
        if what == "undo":
            pc.status = "undone"; s.commit()
            return _back(ref, no)
        ps = s.get(db.ProductionSet, pc.set_id)
        if ps is None or ps.id != (current_set(l).id if current_set(l) else 0):
            pc.status = "undone"; s.commit()
            return _back(ref, no, error="The production drawing changed since this was proposed. Ask again.")
        if ps.released_at is None:
            ps.files, ps.spec, ps.made_by, ps.made_at = pc.files, pc.spec, user.name, db.now()
            pc.status = "accepted"
            s.add(db.Message(order_id=o.id, who="desk", text="Production drawing P%d of Product %d changed by %s (construction only, client drawing unchanged): %s"
                             % (ps.prod_no, l.no, user.name, pc.said or pc.typed)))
        else:
            pc.status = "asked"
            s.add(db.ChangeRequest(kind="production", order_ref=ref, line_no=l.no, product="Product %d: %s" % (l.no, l.name.split("(")[0].strip()),
                                   text="P%d -> P%d: %s" % (ps.prod_no, ps.prod_no + 1, pc.said or pc.typed), asked_by=user.name, note=""))
            s.add(db.Message(order_id=o.id, who="desk", text="%s asked to change the released production drawing P%d of Product %d (becomes P%d): %s. Waiting for an administrator."
                             % (user.name, ps.prod_no, l.no, ps.prod_no + 1, pc.said or pc.typed)))
        s.commit()
    return _back(ref, no)


def apply_approved(cr, who):
    """Called by the Approvals screen when a production change (P -> P+1) is allowed or rejected: makes the next P from the
    accepted trial and sends the stop alert, or drops it."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == cr.order_ref))
        l = next((x for x in (o.lines if o else []) if x.no == cr.line_no), None)
        if l is None:
            return
        pc = s.scalars(select(db.ProdChange).where(db.ProdChange.line_id == l.id, db.ProdChange.status == "asked").order_by(db.ProdChange.id.desc())).first()
        if pc is None:
            return
        if cr.status != "approved":
            pc.status = "rejected"; s.commit(); return
        old = s.get(db.ProductionSet, pc.set_id)
        new = db.ProductionSet(line_id=l.id, revision_id=old.revision_id, kind="PRODUCTION", files=pc.files, details=old.details, engine_version=old.engine_version,
                               made_by=pc.by, prod_no=old.prod_no + 1, spec=pc.spec)
        s.add(new)
        pc.status = "approved"
        alert = "STOP: production drawing of Product %d (%s) changed from P%d to P%d (%s). Use the new sheets; the P%d sheets are withdrawn." % (
            l.no, o.source_ref, old.prod_no, old.prod_no + 1, pc.said or pc.typed, old.prod_no)
        s.add(db.Message(order_id=o.id, who="desk", text=alert + " Allowed by %s." % who))
        from .alerts import stop                                       # decision 12: task + email to each
        stop(s, o, l.no, alert, "prodstop:%d" % pc.id, os.environ.get("DESK_URL", ""))
        s.delete(old)
        s.commit()


@router.post("/orders/{ref}/production/{no}/release")
def release(ref: str, no: int, user=User):
    o, l = _line(ref, no)
    ps = current_set(l)
    if ps is None:
        raise HTTPException(400, "Make the production drawings first")
    with db.Session() as s:
        if s.scalar(select(db.ProdChange.id).where(db.ProdChange.line_id == l.id, db.ProdChange.status.in_(("trial", "asked"))).limit(1)):
            raise HTTPException(400, "A change is waiting: accept or undo it first")
        ps_ = s.get(db.ProductionSet, ps.id)
        if ps_.released_at:
            raise HTTPException(400, "P%d is already released" % ps_.prod_no)
        ps_.released_by, ps_.released_at = user.name, db.now()
        s.add(db.Message(order_id=o.id, who="desk", text="Production drawing P%d of Product %d released to production by %s." % (ps_.prod_no, l.no, user.name)))
        s.commit()
    return _back(ref, no)


@router.get("/orders/{ref}/production/{no}/copy")
def copy_for(ref: str, no: int, dept: str = "all", user=User):
    """The production drawing for one department, stamped "COPY FOR <department>" in the top margin. The stamp is the
    only thing added; the engine's sheets are otherwise as drawn."""
    o, l = _line(ref, no)
    d = next((x for x in DEPTS if x[0] == dept), None)
    if d is None:
        raise HTTPException(404)
    ps = current_set(l, "VENDOR" if dept == "vendor" else "PRODUCTION")
    if ps is None:
        raise HTTPException(400, "Make the subcontractor drawings first" if dept == "vendor" else "Make the production drawings first")
    f = next((x for x in ps.files if d[2] and d[2] in x["title"].upper()), None) or ps.files[0]
    import pymupdf
    out = f["pdf"][:-4] + "-copy-%s.pdf" % dept
    with pymupdf.open(f["pdf"]) as doc:
        stamp = "COPY FOR: %s  ·  P%d  ·  %s" % (d[1].upper(), ps.prod_no, datetime.datetime.now(IST).strftime("%d %b %Y").upper())
        for pg in doc:
            pg.insert_text((24, 14), stamp, fontsize=9, fontname="hebo", color=(0.73, 0.11, 0.11))
        doc.save(out)
    return FileResponse(out, filename="%s-Product%d-P%d-%s.pdf" % (o.source_ref, l.no, ps.prod_no, dept), content_disposition_type="inline")


def _pdf_page(pdf, n):
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404)
    import pymupdf
    out = pdf[:-4] + ".p%d.png" % n
    if not os.path.exists(out):
        with pymupdf.open(pdf) as doc:
            if not (0 <= n < len(doc)):
                raise HTTPException(404)
            doc[n].get_pixmap(dpi=150).save(out)
    return FileResponse(out)


@router.get("/prodchange/{cid}/page/{n}")
def prodchange_page(cid: int, n: int, user=User):
    with db.Session() as s:
        pc = s.get(db.ProdChange, cid)
    if pc is None or not pc.files:
        raise HTTPException(404)
    return _pdf_page(pc.files[0]["pdf"], n)
