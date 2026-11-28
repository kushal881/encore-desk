"""Encore drawing desk: dashboard, orders, questions, previews, changes, issue with numbers and revisions, the steps after
issue, engine requests, product photographs and remakes. Not built yet: Odoo source, client portal, cloud archive."""
import os
from fastapi import FastAPI, Form, HTTPException, Request, Depends, UploadFile, File
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from . import db, spec, engines, finishes, auth, viewer, parts, assistant, specify, changes as chg
import datetime, secrets, io, re
from .reading import read_line, fmt
from .nodrawing import classify
from .sources.sample import SampleSource
from .sources.odoo import OdooSource, OdooError
from .sources.base import Sources

app = FastAPI(title="Encore drawing desk")
T = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
T.env.filters["when"] = lambda d: d.replace(tzinfo=d.tzinfo or datetime.timezone.utc).astimezone(IST).strftime("%d %b %Y, %H:%M") if d else ""
ODOO = OdooSource()
SOURCE = Sources(ODOO if ODOO.configured else None, SampleSource())      # when Odoo is replaced, only this line changes
if auth.ON_SERVER and not os.environ.get("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY must be set on the server")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32), https_only=auth.ON_SERVER, same_site="lax", max_age=12 * 3600)
app.include_router(auth.router)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")   # logo; no client files here
db.init()
engines.start()
with db.Session() as _s:
    finishes.seed(_s)
    auth.seed(_s)
User = Depends(auth.current_user)


@app.exception_handler(auth.NotSignedIn)
def _to_login(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.post("/engines/refresh")
def engines_refresh(user=User):
    """Checks the approved repository for a newer version. Administrator only."""
    if user.role != "admin":
        raise HTTPException(403, "Only an administrator can do this")
    try:
        engines.fetch_latest()
        return RedirectResponse("/", status_code=303)
    except Exception as e:
        raise HTTPException(502, "Could not fetch the approved engines: %s" % str(e)[:200])


@app.get("/health")
def health():
    return {"ok": True, "engines": engines.version()}


STAGES = dict(drawing="Drawings in progress", issued="Issued, to be sent to client", sent="With client for approval",
              approved="Approved by client", production="In production", completed="Completed")


def stage_of(steps, has_issued=False):
    """-> (stage key, client approval still pending?) from the order's log of steps."""
    f = dict(issued=has_issued, sent=False, approved=False, released=False, completed=False)
    for st in steps:
        if st.kind == "issued":
            f.update(issued=True, sent=False, approved=False)       # a new issue needs a new approval
        elif st.kind == "reopened":
            f["completed"] = False
        elif st.kind in f:
            f[st.kind] = True
    key = "completed" if f["completed"] else "production" if f["released"] else "approved" if f["approved"] else "sent" if f["sent"] else "issued" if f["issued"] else "drawing"
    return key, f["issued"] and not f["approved"]


def product_key(name):
    return name.split("(")[0].strip().lower()[:200]


def _save_photo(s, name, data, by):
    """Keeps a photograph for a product name. -> True if the data was a picture."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)); im = im.convert("RGB"); im.thumbnail((900, 900))
    except Exception:
        return False
    key = product_key(name)
    ph = s.scalar(select(db.Photo).where(db.Photo.product == key)) or db.Photo(product=key, path="")
    s.add(ph); s.flush()
    os.makedirs(os.path.join(engines.FILES, "photos"), exist_ok=True)
    ph.path, ph.by, ph.at = os.path.join(engines.FILES, "photos", "%d.jpg" % ph.id), by, db.now()
    im.save(ph.path, "JPEG", quality=85)
    s.commit()
    return True


def _fetch_photos(s, src):
    """Brings each product's photograph from the order's source (Odoo) the first time the order is opened.
    A photograph the desk already has for that product name is kept."""
    source = getattr(src, "_from", None)
    if not hasattr(source, "product_image"):
        return
    for l in src.lines:
        if l.no_drawing or not l.product_ref or s.scalar(select(db.Photo).where(db.Photo.product == product_key(l.name))):
            continue
        try:
            data = source.product_image(l.product_ref)
        except Exception:
            continue                                               # a missing photograph never stops an order from opening
        if data:
            _save_photo(s, l.name, data, "Odoo")


def _sync_questions(s, l):
    """Unanswered questions follow the current reading rules (a line read by an earlier version may be asked something the
    desk now knows, or be offered old choices). Answered questions are never changed. -> True when something changed."""
    if l.revisions:
        return False
    want = {q["key"]: q for q in l.read["questions"]}
    changed = False
    for q in list(l.questions):
        if q.answer:
            continue
        w = want.get(q.key)
        if w is None:
            l.questions.remove(q); s.delete(q); changed = True
        elif (q.text, list(q.options or [])) != (w["text"], w["options"]):
            q.text, q.options = w["text"], w["options"]; changed = True
    have = {q.key for q in l.questions}
    for k, w in want.items():
        if k not in have:
            l.questions.append(db.Question(key=k, text=w["text"], options=w["options"])); changed = True
    return changed


def load(ref):
    """The order from our own database; read from its source and copied in the first time it is opened."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        if o is None:
            try:
                src = SOURCE.get_order(ref)
            except KeyError:
                raise HTTPException(404, "Order %s was not found" % ref)
            except OdooError as e:
                raise HTTPException(502, "Odoo could not be read: %s" % e)
            c = s.scalar(select(db.Client).where(db.Client.name == src.client_name)) or db.Client(name=src.client_name, contact=src.client_contact)
            o = db.Order(client=c, source=src.source, source_ref=src.ref, job_order=src.job_order, salesperson=src.salesperson, delivery=src.delivery)
            total = 0
            for l in src.lines:
                if l.no_drawing:                                   # bought-out, service, installation, delivery: listed, never drawn
                    o.not_drawn.append(db.NoDrawingLine(name=l.name, qty=l.qty, reason=l.no_drawing))
                    continue
                line = db.OrderLine(no=l.no, name=l.name, description=l.description, qty=l.qty)
                for q in read_line(l.name, l.description)["questions"]:
                    line.questions.append(db.Question(key=q["key"], text=q["text"], options=q["options"])); total += 1
                o.lines.append(line)
            n, skipped = len(o.lines), len(o.not_drawn)
            o.messages.append(db.Message(who="desk", text="I have read the %d product%s to draw in this order%s. %s" % (n, "" if n == 1 else "s",
                              " (%d more line%s need%s no drawing)" % (skipped, "" if skipped == 1 else "s", "s" if skipped == 1 else "") if skipped else "",
                              "%d thing%s need%s your answer before I draw." % (total, "" if total == 1 else "s", "s" if total == 1 else "") if total else "Nothing is missing; the drawings can be made.")))
            s.add(o); s.commit()
            _fetch_photos(s, src)
        s.refresh(o)
        _ = o.not_drawn, o.added
        changed = False
        for l in o.lines:
            l.read = read_line(l.name, l.description)
            changed |= _sync_questions(s, l)
            l.size_text = fmt(l.read["size"]) if l.read["size"] else "size not found"
            l.open_count = sum(1 for q in l.questions if not q.answer)
            _ = l.questions, l.drawing, l.changes, l.revisions
            l.confirmed = {}
            for c in l.changes:
                if c.status == "confirmed":
                    l.confirmed.update(c.values)
            l.last_rev = l.revisions[-1] if l.revisions else None
            l.remake = l.confirmed.pop("remake_of", None)
            l.confirmed.pop("use_engine", None)
            # the engine version: an issued product stays on the version it was issued on, until someone moves it on purpose
            l.engine_for = l.last_rev.engine_version if l.last_rev else None
            for c in l.changes:
                if c.status == "confirmed" and "use_engine" in (c.values or {}) and (not l.last_rev or c.at.replace(tzinfo=None) > l.last_rev.issued_at.replace(tzinfo=None)):
                    l.engine_for = c.values["use_engine"]
            l.issued_current = bool(l.last_rev and l.drawing and l.drawing.status == "preview" and l.last_rev.spec.get("model") == l.drawing.spec.get("model")
                                    and l.last_rev.spec.get("swatches") == l.drawing.spec.get("swatches") and l.last_rev.engine_version == l.drawing.engine_version)
            l.can_update_engine = bool(l.last_rev and l.engine_for == l.last_rev.engine_version and __import__("re").fullmatch(r"v\d+", l.last_rev.engine_version or "")
                                       and l.last_rev.engine_version != engines.version() and not l.remake)
            _ = l.sets, l.marks, l.areas
            if l.remake:
                l.open_count = 0                                     # an exact copy of a past drawing needs no answers
            ph = s.scalar(select(db.Photo).where(db.Photo.product == product_key(l.name)))
            l.photo = ph.id if ph else None
            l.photo_v = int(ph.at.timestamp()) if ph else 0
            d_ = l.drawing                                           # marked ready to issue in the viewer (that exact preview)
            l.review = s.scalars(select(db.Review).where(db.Review.line_id == l.id, db.Review.shown == "d%d" % int(d_.made_at.timestamp())).order_by(db.Review.id.desc())).first() \
                if d_ and d_.status == "preview" else None
        if changed:
            s.commit()
            for l in o.lines:
                _ = l.questions
        _ = o.client, o.messages, o.steps
        o.stage, o.approval_pending = stage_of(o.steps, any(l.revisions for l in o.lines))
        o.stage_text = STAGES[o.stage]
        past = s.scalars(select(db.Revision).join(db.OrderLine).join(db.Order).where(db.Order.client_id == o.client_id).order_by(db.Revision.id)).all()
        latest = {}
        for r in past:
            latest[r.number] = r
        o.past = [dict(id=r.id, label="%s Rev %s · %s · %s" % (r.number, r.rev, r.spec.get("item", {}).get("product_name", ""), r.spec.get("item", {}).get("product_code", ""))) for r in latest.values()]
        return o


STEP_TEXT = dict(sent="Sent to the client for approval", approved="Client approval recorded", released="Released to production",
                 completed="Project marked completed", reopened="Project reopened")


def timeline(o):
    """The conversation in the order things happened."""
    ev = [(o.read_at, 0, dict(kind="say", text=o.messages[0].text))]
    for l in o.lines:
        for q in l.questions:
            ev.append((o.read_at, 1 + q.id, dict(kind="question", line=l, q=q)))
            if q.answer:
                ev.append((q.answered_at, q.id, dict(kind="me", text=q.answer, by=q.answered_by)))
        for c in l.changes:
            ev.append((c.at, 2 * c.id, dict(kind="me", text=c.typed, by=c.by, view=l.no if "[Markers:" in c.typed else None)))
            ev.append((c.at, 2 * c.id + 1, dict(kind="change", line=l, c=c)))
    for st in o.steps:
        if st.kind != "issued":
            ev.append((st.at, 10 ** 6, dict(kind="step", text=STEP_TEXT.get(st.kind, st.kind), note=st.note, by=st.by)))
    for m in o.messages[1:]:
        ev.append((m.at, 10 ** 6 + m.id, dict(kind="say" if m.who == "desk" else "me", text=m.text, by=m.who)))
    return [e for _, _, e in sorted(ev, key=lambda x: (x[0].replace(tzinfo=None), x[1]))]


def ctx(user, **kw):
    with db.Session() as s:
        waiting = len(s.scalars(select(db.EngineRequest).where(db.EngineRequest.status == "asked")).all())
    return dict(user=user.name, role=auth.ROLES.get(user.role, user.role), is_admin=user.role == "admin", engine_version=engines.version(), requests_waiting=waiting, **kw)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, show: str = "active", focus: str = "", user=User):
    """The dashboard: every project the desk holds, where each stands, and what is waiting."""
    rows, items, count = [], [], dict(active=0, pending=0, with_client=0, approved=0, production=0, completed=0)
    titles = dict(active="All drawings of active projects", pending="Drawings pending", with_client="Drawings waiting for client approval", approved="Drawings approved, not yet released", production="Drawings in production")
    focus = focus if focus in titles else ""
    with db.Session() as s:
        for o in s.scalars(select(db.Order).order_by(db.Order.id.desc())):
            st, pend = stage_of(o.steps, any(l.revisions for l in o.lines))
            n = len(o.lines)
            issued = sum(1 for l in o.lines if l.revisions)
            failed = sum(1 for l in o.lines if l.drawing and l.drawing.status == "failed")
            last = max([o.read_at] + [x.at for x in o.steps] + [r.issued_at for l in o.lines for r in l.revisions], key=lambda d: d.replace(tzinfo=None))
            if st == "completed":
                count["completed"] += 1
            else:
                count["active"] += 1
                count["pending"] += n - issued
                count["with_client"] += st in ("issued", "sent")
                count["approved"] += st == "approved"
                count["production"] += st == "production"
            if st != "completed":
                for l in o.lines:
                    r = l.revisions[-1] if l.revisions else None
                    hit = dict(active=True, pending=r is None, with_client=bool(r) and st in ("issued", "sent"), approved=bool(r) and st == "approved", production=bool(r) and st == "production").get(focus, False)
                    if hit:
                        can_view = bool(r) or bool(l.drawing and l.drawing.status == "preview")
                        state = "%s Rev %s" % (r.number, r.rev) if r else "Could not be drawn" if l.drawing and l.drawing.status == "failed" else "Preview, not issued" if l.drawing else "Not drawn yet"
                        ph = s.scalar(select(db.Photo).where(db.Photo.product == product_key(l.name)))
                        items.append(dict(ref=o.source_ref, client=o.client.name, no=l.no, name=l.name.split("(")[0].strip(), state=state, stage=STAGES[st], photo=ph.id if ph else None,
                                          thumb=("/revisions/%d/png" % r.id) if r else ("/drawings/%d/png" % l.drawing.id) if can_view else "",
                                          url=("/orders/%s/view/%d" % (o.source_ref, l.no)) if can_view else "/orders/%s/work" % o.source_ref))
            if (st == "completed") == (show == "completed"):
                try:
                    gd = guide(load(o.source_ref))
                except Exception:
                    gd = dict(title="", step=0)
                rows.append(dict(next=gd["title"], ref=o.source_ref, client=o.client.name, n=n, issued=issued, failed=failed, stage=st, stage_text=STAGES[st], pending=pend and st == "production",
                                 salesperson=o.salesperson, last=last, drawings=[dict(number=l.revisions[-1].number, rev=l.revisions[-1].rev, no=l.no) for l in o.lines if l.revisions]))
        known = {o.source_ref for o in s.scalars(select(db.Order))}
    samples = [r for r in SampleSource().list_refs() if r not in known]
    quotes = [q for q in ODOO.open_quotations() if q["ref"] not in known] if ODOO.configured else []
    odoo = (ODOO.status() if ODOO.configured else "Not connected yet.") if user.role == "admin" else ""
    return T.TemplateResponse(request, "dashboard.html", ctx(user, rows=rows, count=count, show=show, samples=samples, odoo=odoo, odoo_on=ODOO.configured, quotes=quotes, stage_note=ODOO.stage_note, focus=focus, items=items, focus_title=titles.get(focus, ""),
                                                                  hide_back=not focus and show == "active"))


@app.post("/open")
def open_order(ref: str = Form(...), user=User):
    return RedirectResponse("/orders/%s" % ref.strip(), status_code=303)


def _ready(l):
    return bool(l.drawing and l.drawing.status == "preview" and not l.open_count and not any(c.status == "asked" for c in l.changes))


GUIDE_STEPS = ["Questions", "Previews", "Issue", "Client approval", "Production", "Completed"]


def guide(o):
    """The one thing to do next on an order, in plain words. -> dict(step, title, text, label, url | post, warn)."""
    ref, work = o.source_ref, "/orders/%s/work" % o.source_ref
    q = sum(l.open_count for l in o.lines)
    asked = sum(1 for l in o.lines for c in l.changes if c.status == "asked")
    nodraw = [l.no for l in o.lines if not l.open_count and not l.drawing]
    unissued = [l.no for l in o.lines if _ready(l) and not l.issued_current]
    failed = [l.no for l in o.lines if l.drawing and l.drawing.status == "failed"]
    names = lambda nos: "product " + ", ".join(map(str, nos)) if len(nos) < len(o.lines) or len(nos) == 1 else "all %d products" % len(nos)
    g = dict(warn="Product %s cannot be drawn yet. Ask for an engine addition in the conversation; the other products can go ahead." % ", ".join(map(str, failed)) if failed else "")
    if o.stage == "completed":
        return dict(g, step=5, title="This project is completed", text="Its drawings are kept. Reopen it only if work has to continue.", label="", url="")
    if q:
        return dict(g, step=0, title="Answer %d question%s" % (q, "" if q == 1 else "s"), text="The desk needs these answers before it can draw. Pick an option or type the answer.", label="Answer the questions", url=work + "#todo")
    if asked:
        return dict(g, step=1, title="Confirm %d change request%s" % (asked, "" if asked == 1 else "s"), text="The desk has said how it understood each change. Say yes to redraw, or no to drop it.", label="Go to the change", url=work + "#todo")
    if nodraw:
        return dict(g, step=1, title="Draw the previews", text="Every question is answered for %s. Drawing a preview sends nothing to the client." % names(nodraw), label="Draw previews", post="/orders/%s/draw" % ref)
    if unissued:
        return dict(g, step=2, title="Check the previews, then issue", text="Open each preview of %s and check it. Issue gives the drawing its number and revision, and cannot be undone." % names(unissued), label="Check and issue", url=work + "#issue")
    if o.stage == "drawing":
        return dict(g, step=1, title="Nothing can be drawn yet", text="See the note below.", label="Open the conversation", url=work)
    if o.stage == "issued":
        return dict(g, step=3, title="Send the drawings to the client", text="Send the issued drawings for approval, then record here how they were sent.", label="Record that they were sent", url="#next")
    if o.stage == "sent":
        return dict(g, step=3, title="Waiting for the client's approval", text="When the client approves, record who approved and how. If they ask for a change, type it as a change request and issue again.", label="Record the approval", url="#next")
    if o.stage == "approved":
        return dict(g, step=4, title="Release to production", text="The client has approved. Releasing opens the production drawings.", label="Release to production", url="#next")
    sets = [x for l in o.lines for x in l.sets if x.kind == "PRODUCTION"]
    stale = any(l.last_rev and x.revision_id != l.last_rev.id for l in o.lines for x in l.sets)
    if not sets or stale:
        return dict(g, step=4, title="Make the production drawings" if not sets else "Make the production drawings again", text="Enter the job order number and deadline. The sheets carry no client details." if not sets else "A newer revision was issued after the production drawings were made.", label="Go to production drawings", url="#sets")
    if o.approval_pending:
        return dict(g, step=4, title="Record the client's approval", text="This order was released before the client approved. Record the approval when it comes.", label="Record the approval", url="#next")
    return dict(g, step=4, title="In production", text="Print the production drawings for the shop floor. Mark the project completed when the order is delivered.", label="Mark completed", url="#next")


def _after(request, ref):
    """Back to the page the action was taken on: the project page or the drawing conversation."""
    from urllib.parse import urlparse
    came = urlparse(request.headers.get("referer", "")).path
    return RedirectResponse("/orders/%s" % ref if came == "/orders/%s" % ref else "/orders/%s/work" % ref, status_code=303)


FLOW = [("drawing", "Drawings"), ("issued", "Issued"), ("sent", "With client"), ("approved", "Approved"), ("production", "In production"), ("completed", "Completed")]


def _bom_files(o):
    with db.Session() as s:
        return s.scalars(select(db.ProductionFile).where(db.ProductionFile.order_id == o.id).order_by(db.ProductionFile.id.desc()).limit(6)).all()


@app.get("/orders/{ref}", response_class=HTMLResponse)
def project(request: Request, ref: str, user=User):
    """The project page: where the order stands, what needs attention, and every product with its drawing."""
    load(ref)
    _, issued_aside = apply_no_drawing(ref)                                  # orders read before the rule existed get it too
    o = load(ref)
    todo = []
    for n, nm, why in issued_aside:
        todo.append(("Product %d (%s) needs no drawing (%s) but its drawing is issued. Removing it is a quotation change: Cancelled stamp and reissue." % (n, nm.split("(")[0].strip(), why.lower()), "See", "/orders/%s/view/%d" % (ref, n)))
    q = sum(l.open_count for l in o.lines)
    if q:
        todo.append(("%d question%s to answer before drawing" % (q, "" if q == 1 else "s"), "Answer", "/orders/%s/work" % ref))
    asked = sum(1 for l in o.lines for c in l.changes if c.status == "asked")
    if asked:
        todo.append(("%d change request%s waiting for your yes or no" % (asked, "" if asked == 1 else "s"), "Confirm", "/orders/%s/work" % ref))
    nodraw = [l.no for l in o.lines if not l.open_count and not l.drawing]
    if nodraw:
        todo.append(("Product %s ready to draw" % ", ".join(map(str, nodraw)), "Draw previews", "/orders/%s/work" % ref))
    failed = [l.no for l in o.lines if l.drawing and l.drawing.status == "failed"]
    if failed:
        todo.append(("Product %s could not be drawn (needs an engine addition)" % ", ".join(map(str, failed)), "Open", "/orders/%s/work" % ref))
    unissued = [l.no for l in o.lines if _ready(l) and not l.issued_current]
    if unissued:
        todo.append(("Product %s has a preview that is not issued" % ", ".join(map(str, unissued)), "Issue", "/orders/%s/work" % ref))
    stale = sorted({l.no for l in o.lines for x in l.sets if l.last_rev and x.revision_id != l.last_rev.id})
    if stale:
        todo.append(("Production drawings of product %s are from an older revision" % ", ".join(map(str, stale)), "Make again", "#sets"))
    with db.Session() as s:
        er = len(s.scalars(select(db.EngineRequest).where(db.EngineRequest.order_ref == ref, db.EngineRequest.status.in_(["asked", "approved"]))).all())
    if er:
        todo.append(("%d engine request%s for this order not built yet" % (er, "" if er == 1 else "s"), "See", "/engine-requests"))
    keys = [k for k, _ in FLOW]
    flow = [(t, "now" if k == o.stage else "done" if keys.index(k) < keys.index(o.stage) else "") for k, t in FLOW]
    return T.TemplateResponse(request, "project.html", ctx(user, o=o, todo=todo, flow=flow, remove_reasons=REMOVE_REASONS, g=guide(o), steps=GUIDE_STEPS, tab="overview", latest=engines.version(), issued=sum(1 for l in o.lines if l.revisions),
                              prod_sets=[x for l in o.lines for x in l.sets if x.kind == "PRODUCTION"], vendor_sets=[x for l in o.lines for x in l.sets if x.kind == "VENDOR"],
                              bom_files=_bom_files(o),
                              today=datetime.date.today().strftime("%d %b %Y").upper()))


@app.get("/orders/{ref}/work", response_class=HTMLResponse)
def order(request: Request, ref: str, user=User):
    o = load(ref)
    client_no = o.client.folder_no
    serial0 = 0
    if client_no:
        with db.Session() as s:
            serial0 = len({r.number for r in s.scalars(select(db.Revision).join(db.OrderLine).join(db.Order).where(db.Order.client_id == o.client_id))})
    to_issue = [l for l in o.lines if _ready(l) and not l.issued_current]
    plan = []
    for l in to_issue:
        if l.last_rev:
            plan.append("Product %d: %s becomes Rev %s" % (l.no, l.last_rev.number, chr(ord(l.last_rev.rev) + 1)))
        else:
            serial0 += 1
            plan.append("Product %d: new drawing %s" % (l.no, "Encore-%s-%s-%02d Rev A" % (datetime.date.today().strftime("%b-%Y"), client_no, serial0) if client_no else "number given after you enter the folder number"))
    return T.TemplateResponse(request, "order.html", ctx(user, o=o, g=guide(o), steps=GUIDE_STEPS, tab="work", plan=plan, events=timeline(o), open_total=sum(l.open_count for l in o.lines),
                              can_draw=any(l.open_count == 0 for l in o.lines), drawn=sum(1 for l in o.lines if l.drawing and l.drawing.status == "preview"),
                              can_issue=True, not_ready=[l.no for l in o.lines if not _ready(l)],
                              needs_issue=any(_ready(l) and not l.issued_current for l in o.lines), latest=engines.version(),
                              prod_sets=[x for l in o.lines for x in l.sets if x.kind == "PRODUCTION"], vendor_sets=[x for l in o.lines for x in l.sets if x.kind == "VENDOR"],
                              bom_files=_bom_files(o),
                              today=datetime.date.today().strftime("%d %b %Y").upper()))


@app.post("/orders/{ref}/answer/{qid}")
def answer(ref: str, qid: int, value: str = Form(...), back: str = Form(""), user=User):
    value = value.strip()
    if not value:
        raise HTTPException(400, "Enter an answer first")
    with db.Session() as s:
        q = s.get(db.Question, qid)
        if q is None:
            raise HTTPException(404)
        line = s.get(db.OrderLine, q.line_id)
        order_ = s.get(db.Order, line.order_id)
        if order_.source_ref != ref:
            raise HTTPException(404)
        q.answer, q.answered_by, q.answered_at = value, user.name, db.now()
        s.commit()
        no = line.no
    if back == "specify":
        return RedirectResponse("/orders/%s/specify/%d" % (ref, no), status_code=303)
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


def make_spec(s, o, l, issue=None, edir=None, extra=None):
    """The specification for one product: an exact copy of a past drawing when it is a remake, otherwise built from the order.
    extra = values not confirmed yet (an assistant's trial). Values named model.* go straight into the product file's model."""
    if l.remake:
        past = s.get(db.Revision, l.remake)
        if past is None:
            raise spec.CannotDraw("The past drawing chosen for this remake was not found.")
        return spec.remake(o, l, past.spec, l.no, issue=issue)
    values = dict(l.confirmed, **(extra or {}))
    sp = spec.build(edir or engines.ENGINES_DIR, o, l, l.read, {q.key: q.answer for q in l.questions}, l.no, changes=values,
                    finish=lambda text, kind: finishes.lookup(s, text, kind), issue=issue)
    sp = assistant.apply(sp, {k: v for k, v in values.items() if k.startswith("model.")})
    areas = [(a.area, int(a.qty)) for a in (getattr(l, "areas", None) or [])]
    if areas:                                                          # Specify: areas per product, adding up to the qty
        sp["item"]["area"] = specify.area_text(areas, l.qty)
    return sp


def _draw_lines(o, s, who, only=None):
    """Runs the approved engine for every line whose questions are all answered. Each result is a preview."""
    for l in o.lines:
        if l.open_count or (only and l.no not in only):
            continue
        line = s.get(db.OrderLine, l.id)
        d = line.drawing or db.Drawing()
        line.drawing = d
        edir, ver = engines.dir_for(l.engine_for)
        d.engine_version, d.made_by, d.made_at, d.error, d.files = ver, who, db.now(), "", {}
        try:
            d.spec = make_spec(s, o, l, edir=edir)
            d.files = engines.draw(d.spec, "%d/line%d/preview" % (o.id, l.no), edir)
            d.status = "preview"
        except (spec.CannotDraw, RuntimeError) as e:
            d.status, d.error = "failed", str(e)
            d.files = {"checks": e.checks} if isinstance(e, engines.EngineFailed) else {}
    s.commit()


@app.post("/orders/{ref}/draw")
def draw(ref: str, no: int = Form(0), user=User):
    """Draws the previews. With no = a product number (from the viewer), only that product, and back to its viewer."""
    o = load(ref)
    with db.Session() as s:
        _draw_lines(o, s, user.name, only={no} if no else None)
    return RedirectResponse("/orders/%s/view/%d" % (ref, no) if no else "/orders/%s/work" % ref, status_code=303)


@app.post("/orders/{ref}/change")
def change(ref: str, text: str = Form(...), line: int = Form(0), shown: str = Form(""), user=User):
    """A typed change request. It is read, said back, and applied only after the user confirms."""
    text = text.strip()
    if not text:
        raise HTTPException(400, "Type the change first")
    o = load(ref)
    lines = [(l.no, l.read["family"]) for l in o.lines]
    with db.Session() as s:
        no = line if line in [n for n, _ in lines] else chg.which_line(text, lines)
        if no is None:
            s.add(db.Message(order_id=o.id, who=user.name, text=text))
            s.add(db.Message(order_id=o.id, who="desk", text="Which product is this for? Use the change box under the drawing of that product, or start with its number, for example: Product 2: " + text))
        else:
            l = next(x for x in o.lines if x.no == no)
            values, said = chg.understand(text, l.read["family"])
            if l.remake and values:
                values, said = None, "This product is an exact remake of a past drawing, so it cannot be changed here."
            used = [m for m in l.marks if shown and m.shown == shown and re.search(r"(?<![\w.])%d(?![\w.])" % m.no, text)]
            cache = {}
            def marker_text(m):
                where = _mark_part(l, m, cache)
                note = m.label or "marked on the drawing"
                return "%d = %s" % (m.no, note if not where or where.split(" (")[0].lower() in note.lower() else "%s, on %s" % (note, where))
            typed = text + (" [Markers: %s]" % "; ".join(marker_text(m) for m in used) if used else "")
            s.add(db.Change(line_id=l.id, typed=typed, understood=said, values=values or {}, status="asked" if values else "cannot", by=user.name))
        s.commit()
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


@app.post("/orders/{ref}/change/{cid}/{decision}")
def decide(ref: str, cid: int, decision: str, user=User):
    if decision not in ("yes", "no"):
        raise HTTPException(404)
    o = load(ref)
    with db.Session() as s:
        c = s.get(db.Change, cid)
        line = next((l for l in o.lines if c and l.id == c.line_id), None)
        if line is None or c.status != "asked":
            raise HTTPException(404)
        c.status = "confirmed" if decision == "yes" else "rejected"
        s.commit()
        if decision == "yes":
            o = load(ref)
            _draw_lines(o, s, user.name, only={line.no})
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


@app.post("/orders/{ref}/issue")
def issue(ref: str, folder_no: str = Form(""), user=User):
    """Issues every previewed line whose specification differs from its last issued revision. An issued revision is never changed."""
    o = load(ref)
    ready = [l for l in o.lines if _ready(l)]
    if not ready:
        raise HTTPException(400, "No product is ready to issue: a product needs a preview and no unanswered question or unconfirmed change")
    with db.Session() as s:
        client = s.get(db.Client, o.client.id)
        folder_no = folder_no.strip() or client.folder_no
        if not folder_no.isdigit():
            raise HTTPException(400, "Enter the client's folder number (digits only)")
        client.folder_no = "%02d" % int(folder_no)
        today = datetime.date.today().strftime("%d %b %Y").upper()
        used = [int(r.number.rsplit("-", 1)[1]) for r in s.scalars(select(db.Revision).join(db.OrderLine).join(db.Order).where(db.Order.client_id == client.id))]
        nxt, done = max(used, default=0) + 1, []
        for l in ready:
            answers = {q.key: q.answer for q in l.questions}
            model_now = l.drawing.spec.get("model")
            if l.issued_current:
                continue                                             # nothing changed since the last issue
            edir, ver = engines.dir_for(l.engine_for)
            if l.last_rev:
                serial = l.last_rev.number.rsplit("-", 1)[1]
                rev = chr(ord(l.last_rev.rev) + 1)
                new = [c for c in l.changes if c.status == "confirmed" and c.at.replace(tzinfo=None) > l.last_rev.issued_at.replace(tzinfo=None)]
                reason = "; ".join(c.understood for c in new) or "Reissued"
                short = ("; ".join(chg.compact(c.values) for c in new) or "Reissued")
                revs = l.last_rev.spec["revisions"] + [dict(rev=rev, date=today, desc=short if len(short) <= 40 else short[:37] + "...", by=user.name.split()[0])]
            else:
                serial, nxt = "%02d" % nxt, nxt + 1
                rev, reason = "A", "Initial issue for client approval"
                if l.remake:
                    pr = s.get(db.Revision, l.remake)
                    reason = "Remake of %s Rev %s" % (pr.number, pr.rev) if pr else reason
                revs = [dict(rev="A", date=today, desc=reason if len(reason) <= 40 else reason[:37] + "...", by="")]
            sp = make_spec(s, o, l, issue=dict(folder=client.folder_no, serial=serial, revisions=revs), edir=edir)
            files = engines.draw(sp, "%d/line%d/issued/rev%s" % (o.id, l.no, rev), edir)
            number = "Encore-%s-%s-%s-%s" % (datetime.datetime.strptime(revs[0]["date"], "%d %b %Y").strftime("%b"), revs[0]["date"][-4:], client.folder_no, serial)
            s.add(db.Revision(line_id=l.id, number=number, rev=rev, reason=reason, engine_version=ver, spec=sp, files=files, issued_by=user.name))
            done.append("%s Rev %s" % (number, rev))
        s.add(db.Message(order_id=o.id, who="desk", text=("Issued: " + ", ".join(done) + ". Next step: send them to the client for approval (see Next step on the right).") if done
                         else "Nothing to issue: no product has changed since its last issued revision."))
        if done:
            s.add(db.Step(order_id=o.id, kind="issued", note=", ".join(done), by=user.name))
        s.commit()
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


def _big(pdf_path, page=0):
    """A sharp picture of one sheet for viewing inside the desk, made from the PDF once and kept beside it."""
    out = pdf_path[:-4] + (".view.png" if page == 0 else ".view%d.png" % page)
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(pdf_path):
        import pymupdf
        with pymupdf.open(pdf_path) as doc:
            if not 0 <= page < len(doc):
                raise HTTPException(404)
            doc[page].get_pixmap(dpi=220).save(out)
    return out


def _pages(pdf_path):
    """Number of sheets in a PDF."""
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        return len(doc)


@app.get("/revisions/{rid}/{kind}")
def revision_file(rid: int, kind: str, download: int = 0, page: int = 0, user=User):
    if kind not in ("png", "pdf", "dxf", "big"):
        raise HTTPException(404)
    with db.Session() as s:
        r = s.get(db.Revision, rid)
        path, name = (r.files.get("pdf" if kind == "big" else kind), "%s-Rev%s.%s" % (r.number, r.rev, kind)) if r else (None, "")
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    if kind == "big":
        return FileResponse(_big(path, page))
    return FileResponse(path, filename=None if kind == "png" else name, content_disposition_type="attachment" if download or kind == "dxf" else "inline")


@app.get("/drawings/{did}/{kind}")
def drawing_file(did: int, kind: str, download: int = 0, page: int = 0, user=User):
    if kind not in ("png", "pdf", "dxf", "big"):
        raise HTTPException(404)
    with db.Session() as s:
        d = s.get(db.Drawing, did)
        path = d.files.get("pdf" if kind == "big" else kind) if d else None
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    if kind == "big":
        return FileResponse(_big(path, page))
    return FileResponse(path, filename=None if kind == "png" else "PREVIEW-drawing-%d.%s" % (did, kind), content_disposition_type="attachment" if download or kind == "dxf" else "inline")


def _part_map(l, key):
    """The engine's part map for the drawing a marker key points at (sheet 1 only). None when there is none."""
    m = re.fullmatch(r"([rd])(\d+)", key or "")
    if not m:
        return None
    if m.group(1) == "r":
        r = next((x for x in l.revisions if x.id == int(m.group(2))), None)
        return parts.load(r.files.get("parts")) if r else None
    d = l.drawing
    return parts.load(d.files.get("parts")) if d and int(d.made_at.timestamp()) == int(m.group(2)) else None


def _mark_part(l, m, cache=None):
    """Plain words for the part a marker sits on, e.g. 'Screen (front view)', or ''."""
    cache = {} if cache is None else cache
    if m.shown not in cache:
        cache[m.shown] = _part_map(l, m.shown)
    hit = parts.part_at(cache[m.shown], m.x / 100, m.y / 100)
    return hit["where"] if hit else ""


def _view_url(ref, no, rev=0, sheet=1, mode="client"):
    q = [("rev", rev), ("s", sheet if sheet > 1 else 0), ("mode", "prod" if mode == "prod" else "")]
    q = "&".join("%s=%s" % (k, v) for k, v in q if v)
    return "/orders/%s/view/%d%s" % (ref, no, "?" + q if q else "")


def _shown(l, rev=0):
    """Which drawing of a product the viewer shows. -> dict(r, src, title, key, stamp, preview, failed, pdf, checks)."""
    r = next((x for x in l.revisions if x.id == rev), None)
    d = l.drawing
    if r is None and d is not None and d.status == "preview" and l.issued_current:
        r = l.last_rev                                                   # the preview is the same as the issued drawing
    if r is None and d is not None and d.status in ("preview", "failed"):
        stamp = int(d.made_at.timestamp())
        failed = d.status == "failed"
        return dict(r=None, src="/drawings/%d" % d.id, title="Could not be drawn" if failed else "Preview, not issued", key="d%d" % stamp, stamp=stamp,
                    preview=not failed, failed=failed, pdf=d.files.get("pdf"), checks=d.files.get("checks"), engine_version=d.engine_version)
    r = r or l.last_rev
    if r is None:
        raise HTTPException(404)
    return dict(r=r, src="/revisions/%d" % r.id, title="%s Rev %s" % (r.number, r.rev), key="r%d" % r.id, stamp=int(r.issued_at.timestamp()),
                preview=False, failed=False, pdf=r.files.get("pdf"), checks=r.files.get("checks"), engine_version=r.engine_version)


def _production_view(l, sh):
    """The production drawing for the client drawing shown: the real production set when it has been made from this
    revision, otherwise a production preview with placeholders. -> dict(kind real | preview | none, ...)."""
    r = sh["r"]
    real = next((x for x in reversed(l.sets) if r and x.kind == "PRODUCTION" and x.revision_id == r.id and x.files), None)
    if real:
        f = real.files[0]
        return dict(kind="real", img="/sets/%d/0/page/%%d" % real.id, pages=f["pages"], pdf="/sets/%d/0/pdf" % real.id, stamp=int(real.made_at.timestamp()),
                    text="Job order %s · deadline %s" % (real.details.get("job_order", ""), real.details.get("deadline", "")))
    with db.Session() as s:
        pp = s.scalars(select(db.ProdPreview).where(db.ProdPreview.line_id == l.id, db.ProdPreview.shown == sh["key"]).order_by(db.ProdPreview.id.desc())).first()
    if pp and not pp.error and os.path.exists(pp.pdf):
        return dict(kind="preview", img="/prodpreview/%d/page/%%d" % pp.id, pages=pp.pages, pdf="/prodpreview/%d/pdf" % pp.id, stamp=int(pp.made_at.timestamp()),
                    text="Job order and dates are placeholders until the order is released.")
    return dict(kind="none", error=pp.error if pp else "", pages=1)


@app.get("/orders/{ref}/view/{no}", response_class=HTMLResponse)
def view(request: Request, ref: str, no: int, rev: int = 0, s: int = 1, mode: str = "client", compare: int = 0, user=User):
    """The drawing viewer: one product's drawing, every sheet, client or production drawing, numbered markers,
    the automatic checks and the one next step."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not (l.drawing or l.revisions):
        raise HTTPException(404)
    sh = _shown(l, rev)
    mode = "prod" if mode == "prod" and not sh["failed"] else "client"
    prod = _production_view(l, sh) if mode == "prod" else None
    if prod:
        pages = prod["pages"]
    else:
        pages = _pages(sh["pdf"]) if sh["pdf"] and os.path.exists(sh["pdf"]) else 1
    sheet = max(1, min(s, pages))
    if prod is None:
        img = None if sh["failed"] else "%s/big?page=%d&v=%d" % (sh["src"], sheet - 1, sh["stamp"])
    else:
        img = (prod["img"] % (sheet - 1)) + "?v=%d" % prod["stamp"] if prod["kind"] != "none" else None
    skey = viewer.sheet_key(sh["key"], sheet)
    review = None
    if sh["preview"]:
        with db.Session() as ss:
            review = ss.scalars(select(db.Review).where(db.Review.line_id == l.id, db.Review.shown == sh["key"]).order_by(db.Review.id.desc())).first()
    asked = any(c.status == "asked" for c in l.changes)
    drawn = [x.no for x in o.lines if x.revisions or x.drawing]
    i = drawn.index(l.no)
    step = viewer.step_of(o.stage, l)
    rev_q = rev if rev else 0
    url = lambda **kw: _view_url(ref, no, kw.get("rev", rev_q), kw.get("s", sheet), kw.get("mode", mode))
    marks = [m for m in l.marks if m.shown == skey] if mode == "client" else []
    cache = {}
    for m in marks:
        m.part = _mark_part(l, m, cache)
    with db.Session() as ss:
        turns = ss.scalars(select(db.AiTurn).where(db.AiTurn.line_id == l.id).order_by(db.AiTurn.id.desc()).limit(12)).all()[::-1]
    for tr in turns:
        tr.rows = viewer.check_rows(tr.files.get("checks")) if tr.status in ("trial", "failed") else []
        tr.can_accept = tr.status == "trial" and viewer.all_pass(tr.files.get("checks")) and tr.shown == sh["key"]
    cmp_ = next((tr for tr in turns if tr.id == compare and tr.files.get("pdf")), None)
    box = _changed_box(sh, cmp_) if cmp_ else None
    return T.TemplateResponse(request, "view.html", ctx(user, o=o, l=l, tab="p%d" % l.no, sh=sh, src=sh["src"], title=sh["title"], shown=sh["r"], key=skey, base_key=sh["key"],
                              panel=_panel_state(o, l, sh, mode), turns=turns, cmp=cmp_, box=box, ai_on=assistant.configured(),
                              rev_q=rev_q, mode=mode, prod=prod, img=img, sheet=sheet, pages=pages, url=url, step=step, steps=viewer.STEPS,
                              prev_no=drawn[i - 1] if i > 0 else None, next_no_=drawn[i + 1] if i + 1 < len(drawn) else None, count=len(drawn), pos=i + 1,
                              checks=viewer.check_rows(sh["checks"]), nxt=viewer.next_step(l, sh["preview"], sh["checks"], review, asked, mode), review=review,
                              marks=marks, next_no=max([m.no for m in l.marks], default=0) + 1, part_map=_part_map(l, skey) is not None))


# ---- the AI change panel ---------------------------------------------------------------------------------------------
def _panel_state(o, l, sh, mode):
    """Whether the change panel is open on the drawing shown. -> "" when it is, otherwise the reason in plain words."""
    if mode == "prod":
        return "Construction changes are made on the production screen."
    if l.remake:
        return "This product is an exact remake of a past drawing, so it is not changed here."
    if sh["r"] is not None and not l.issued_current and l.drawing and l.drawing.status == "preview":
        return "This is an older issued revision. Open the current drawing to change it."
    if l.revisions and o.stage in ("approved", "production", "completed") and not o.approval_pending:
        return "The client has approved this drawing, so it is locked. A change now needs Kushal's permission (a change after approval)."
    return ""


def _changed_box(sh, tr):
    """Where the trial differs from the drawing shown: per cent box on the sheet, or None. Compares sheet 1 pictures."""
    try:
        from PIL import Image, ImageChops
        a = Image.open(_big(sh["pdf"], 0)).convert("L")
        b = Image.open(_big(tr.files["pdf"], 0)).convert("L")
        if a.size != b.size:
            return None
        bb = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0).getbbox()
    except Exception:
        return None
    if not bb:
        return None
    W, H = a.size
    pad = 0.01
    return dict(x=max(0, bb[0] / W - pad) * 100, y=max(0, bb[1] / H - pad) * 100, w=min(1, (bb[2] - bb[0]) / W + 2 * pad) * 100, h=min(1, (bb[3] - bb[1]) / H + 2 * pad) * 100)


@app.post("/orders/{ref}/view/{no}/ai")
def ai_ask(ref: str, no: int, text: str = Form(...), marker: int = Form(0), user=User):
    """The user asks the assistant about this product. A change is drawn as a trial; the drawing stays as it is until
    someone presses Accept."""
    text = text.strip()
    if not text:
        raise HTTPException(400, "Type the change or the question first")
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not (l.drawing or l.revisions):
        raise HTTPException(404)
    sh = _shown(l)
    why = _panel_state(o, l, sh, "client")
    if why or sh["failed"]:
        raise HTTPException(400, why or "This drawing could not be made. Ask for an engine addition in Questions and changes.")
    cache, marks = {}, []
    for m in l.marks:
        if m.shown.split("s")[0] == sh["key"]:
            where = _mark_part(l, m, cache)
            marks.append("%d = %s%s" % (m.no, m.label or "marked on the drawing", ", on " + where if where and where.split(" (")[0].lower() not in (m.label or "").lower() else ""))
    if marker:
        text = "At marker %d: %s" % (marker, text)
    current = (sh["r"].spec if sh["r"] else l.drawing.spec) or {}
    edir, ver = engines.dir_for(l.engine_for)
    with db.Session() as s:
        history = [(t.typed, t.said or t.answer) for t in s.scalars(select(db.AiTurn).where(db.AiTurn.line_id == l.id).order_by(db.AiTurn.id))]
    product = dict(product="Product %d" % l.no, name=l.name.split("(")[0].strip(), family=l.read["family"], qty=l.qty,
                   issued=("Rev %s" % l.last_rev.rev) if l.last_rev else "not issued")
    reply = assistant.ask(text, product, current.get("model", {}), marks, edir, history)
    tr = db.AiTurn(line_id=l.id, shown=sh["key"], typed=text, kind=reply["kind"], said=reply["said"], values=reply["values"], by=user.name,
                   answer=reply["answer"] + (" (Not used: %s.)" % "; ".join(reply["problems"]) if reply["problems"] and reply["kind"] == "change" else ""))
    tr.status = reply["kind"] if reply["kind"] != "change" else "trial"
    if reply["kind"] == "change":
        with db.Session() as s:
            try:
                tr.spec = make_spec(s, o, l, edir=edir, extra=reply["values"])
                s.add(tr); s.commit()                                         # an id for the trial's folder
                tr.files = engines.draw(tr.spec, "%d/line%d/trial/%d" % (o.id, l.no, tr.id), edir)
                tr.engine_version = ver
            except (spec.CannotDraw, RuntimeError) as e:
                tr.status, tr.error = "failed", str(e)
                tr.files = {"checks": e.checks} if isinstance(e, engines.EngineFailed) else {}
            s.merge(tr); s.commit()
    else:
        with db.Session() as s:
            s.add(tr); s.commit()
    return RedirectResponse(_view_url(ref, no) + "#panel", status_code=303)


def _turn(o, no, tid):
    l = next((x for x in o.lines if x.no == no), None)
    with db.Session() as s:
        tr = s.get(db.AiTurn, tid)
    if l is None or tr is None or tr.line_id != l.id:
        raise HTTPException(404)
    return l, tr


@app.post("/orders/{ref}/view/{no}/ai/{tid}/accept")
def ai_accept(ref: str, no: int, tid: int, user=User):
    """Accept a trial: it becomes this product's drawing and the change is recorded as confirmed. On an issued drawing,
    the next issue makes a new revision (the usual rule)."""
    o = load(ref)
    l, tr = _turn(o, no, tid)
    sh = _shown(l)
    if tr.status != "trial":
        raise HTTPException(400, "This proposal is not waiting for a decision")
    if _panel_state(o, l, sh, "client"):
        raise HTTPException(400, _panel_state(o, l, sh, "client"))
    if tr.shown != sh["key"]:
        raise HTTPException(400, "The drawing has changed since this was proposed. Ask again.")
    if not viewer.all_pass(tr.files.get("checks")):
        raise HTTPException(400, "The trial drawing has not passed every automatic check, so it cannot be accepted")
    with db.Session() as s:
        c = db.Change(line_id=l.id, typed=tr.typed, understood=tr.said or chg.compact(tr.values), values=tr.values, status="confirmed", by=user.name)
        s.add(c); s.flush()
        line = s.get(db.OrderLine, l.id)
        d = line.drawing or db.Drawing()
        line.drawing = d
        d.status, d.error, d.spec, d.files, d.engine_version, d.made_by, d.made_at = "preview", "", tr.spec, tr.files, tr.engine_version, user.name, db.now()
        t_ = s.get(db.AiTurn, tr.id)
        t_.status, t_.change_id = "accepted", c.id
        s.add(db.Message(order_id=o.id, who="desk", text="Product %d: %s. Accepted by %s from the change panel%s." % (
            l.no, tr.said or chg.compact(tr.values), user.name, "; issuing it will make a new revision" if l.revisions else "")))
        s.commit()
    return RedirectResponse(_view_url(ref, no) + "#panel", status_code=303)


@app.post("/orders/{ref}/view/{no}/ai/{tid}/undo")
def ai_undo(ref: str, no: int, tid: int, user=User):
    """Undo: drop a trial, or take back an accepted change that has not been issued yet (the product is drawn again without it)."""
    o = load(ref)
    l, tr = _turn(o, no, tid)
    if tr.status == "trial":
        with db.Session() as s:
            s.get(db.AiTurn, tr.id).status = "undone"; s.commit()
        return RedirectResponse(_view_url(ref, no) + "#panel", status_code=303)
    if tr.status != "accepted":
        raise HTTPException(400, "There is nothing to undo here")
    c = next((x for x in l.changes if x.id == tr.change_id), None)
    if c is None or (l.last_rev and c.at.replace(tzinfo=None) < l.last_rev.issued_at.replace(tzinfo=None)) or l.issued_current:
        raise HTTPException(400, "This change has been issued already. A further change makes a new revision instead.")
    if _panel_state(o, l, _shown(l), "client"):
        raise HTTPException(400, _panel_state(o, l, _shown(l), "client"))
    with db.Session() as s:
        s.get(db.Change, c.id).status = "withdrawn"
        s.get(db.AiTurn, tr.id).status = "withdrawn"
        s.add(db.Message(order_id=o.id, who="desk", text="Product %d: %s. Undone by %s." % (l.no, tr.said or chg.compact(tr.values), user.name)))
        s.commit()
    o = load(ref)
    with db.Session() as s:
        _draw_lines(o, s, user.name, only={no})
    return RedirectResponse(_view_url(ref, no) + "#panel", status_code=303)


@app.get("/ai/{tid}/{kind}")
def ai_file(tid: int, kind: str, page: int = 0, user=User):
    """The trial drawing of a proposal (picture or PDF)."""
    with db.Session() as s:
        tr = s.get(db.AiTurn, tid)
    path = (tr.files or {}).get("pdf") if tr else None
    if kind not in ("big", "pdf") or not path or not os.path.exists(path):
        raise HTTPException(404)
    if kind == "big":
        return FileResponse(_big(path, page))
    return FileResponse(path, filename="TRIAL-not-for-issue.pdf", content_disposition_type="inline")


# ---- Specify (screen 4) ------------------------------------------------------------------------------------------------
@app.get("/orders/{ref}/specify")
def specify_first(ref: str, user=User):
    """Specify opens on the first product that still needs something."""
    o = load(ref)
    if not o.lines:
        return RedirectResponse("/orders/%s" % ref, status_code=303)
    first = next((l for l in o.lines if specify.status(l) == "todo"), o.lines[0])
    return RedirectResponse("/orders/%s/specify/%d" % (ref, first.no), status_code=303)


@app.get("/orders/{ref}/specify/{no}", response_class=HTMLResponse)
def specify_page(request: Request, ref: str, no: int, area_error: str = "", user=User):
    """One product: what came from Odoo, its approved template, areas, what is still missing, and what will be built."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None:
        raise HTTPException(404)
    built, cannot = None, ""
    if not l.open_count:
        with db.Session() as s:
            try:
                built = make_spec(s, o, l, edir=engines.dir_for(l.engine_for)[0])
            except spec.CannotDraw as e:
                cannot = str(e)
    family = l.read["family"]
    if family == "other":
        family = spec.TYPE_BY_ANSWER.get(next((q.answer for q in l.questions if q.key == "family" and q.answer), "").strip().lower(), "other")
    template = spec.TEMPLATES.get((family, spec.variant(family, l.name, l.read["fields"])), "")
    if l.drawing is not None and l.drawing.status == "failed" and not cannot:
        cannot = l.drawing.error
    i = [x.no for x in o.lines].index(no)
    nxt = next((x.no for x in o.lines[i + 1:] + o.lines[:i] if specify.status(x) == "todo"), None)
    return T.TemplateResponse(request, "specify.html", ctx(user, o=o, l=l, tab="specify", template=template, built=built, cannot=cannot,
                              summary=specify.summary(built) if built else [], statuses={x.no: specify.status(x) for x in o.lines},
                              done=sum(1 for x in o.lines if specify.status(x) == "done"), pos=i + 1, next_todo=nxt, area_error=area_error,
                              areas=[(a.area, int(a.qty)) for a in l.areas] or ([(l.read["area"], int(l.qty))] if l.read["area"] else [])))


@app.post("/orders/{ref}/specify/{no}/areas")
async def specify_areas(request: Request, ref: str, no: int, user=User):
    """Areas for one product (optional). Several areas, each with a quantity; together they must make the product's qty."""
    form = await request.form()
    rows = list(zip(form.getlist("area"), form.getlist("qty")))
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None:
        raise HTTPException(404)
    if l.issued_current:
        raise HTTPException(400, "This product's drawing is issued. A change of areas makes a new revision: ask for it in the change panel.")
    clean, problem = specify.check_areas(rows, l.qty)
    if problem:
        from urllib.parse import quote
        return RedirectResponse("/orders/%s/specify/%d?area_error=%s#areas" % (ref, no, quote(problem)), status_code=303)
    with db.Session() as s:
        line = s.get(db.OrderLine, l.id)
        line.areas.clear()
        for a, n in clean:
            line.areas.append(db.LineArea(area=a, qty=n, by=user.name))
        s.commit()
    return RedirectResponse("/orders/%s/specify/%d#areas" % (ref, no), status_code=303)


@app.post("/orders/{ref}/view/{no}/ready")
def mark_ready(ref: str, no: int, shown: str = Form(...), user=User):
    """Marks the preview shown as checked and ready to issue. Only when every automatic check passed."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not l.drawing or l.drawing.status != "preview" or l.issued_current:
        raise HTTPException(400, "There is no preview of this product to mark")
    sh = _shown(l)
    if shown != sh["key"]:
        raise HTTPException(400, "The drawing has been redrawn since you opened it. Look at the new one first.")
    if not viewer.all_pass(sh["checks"]):
        raise HTTPException(400, "This drawing has not passed every automatic check, so it cannot be marked ready")
    if any(c.status == "asked" for c in l.changes):
        raise HTTPException(400, "Confirm or drop the change request for this product first")
    with db.Session() as s:
        s.add(db.Review(line_id=l.id, shown=sh["key"], by=user.name))
        s.add(db.Message(order_id=o.id, who="desk", text="Product %d checked in the viewer and marked ready to issue by %s." % (l.no, user.name)))
        s.commit()
    return RedirectResponse(_view_url(ref, no), status_code=303)


@app.post("/orders/{ref}/view/{no}/production")
def production_preview(ref: str, no: int, rev: int = Form(0), user=User):
    """Makes a production preview of the client drawing shown, with placeholders for the job order and deadline.
    It is only for looking at: it is never issued and never reaches production."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not (l.drawing or l.revisions):
        raise HTTPException(404)
    sh = _shown(l, rev)
    if sh["failed"]:
        raise HTTPException(400, "This drawing could not be made, so there is no production drawing")
    sp = __import__("copy").deepcopy(sh["r"].spec if sh["r"] else l.drawing.spec)
    sp["drawing_set"], sp["production"] = "PRODUCTION", viewer.placeholders(o.job_order)
    edir, ver = engines.dir_for(sh["engine_version"] or None)
    pp = db.ProdPreview(line_id=l.id, shown=sh["key"], engine_version=ver, made_by=user.name)
    try:
        files = engines.draw_set(sp, "%d/line%d/prodpreview/%s" % (o.id, l.no, sh["key"]), edir)
        pp.pdf, pp.pages = files[0]["pdf"], files[0]["pages"]
    except RuntimeError as e:
        pp.error = str(e)[:400]
    with db.Session() as s:
        s.add(pp); s.commit()
    return RedirectResponse(_view_url(ref, no, rev, 1, "prod"), status_code=303)


def _prodpreview(pid):
    with db.Session() as s:
        pp = s.get(db.ProdPreview, pid)
    if pp is None or pp.error or not os.path.exists(pp.pdf):
        raise HTTPException(404)
    return pp


@app.get("/prodpreview/{pid}/pdf")
def prodpreview_pdf(pid: int, user=User):
    return FileResponse(_prodpreview(pid).pdf, filename="PRODUCTION-PREVIEW-not-for-issue.pdf", content_disposition_type="inline")


@app.get("/prodpreview/{pid}/page/{n}")
def prodpreview_page(pid: int, n: int, user=User):
    pp = _prodpreview(pid)
    if not 0 <= n < pp.pages:
        raise HTTPException(404)
    return FileResponse(_big(pp.pdf, n))


@app.post("/orders/{ref}/step/{kind}")
def step(request: Request, ref: str, kind: str, note: str = Form(""), user=User):
    """The steps after issue. Each is recorded with who did it and when."""
    if kind not in ("sent", "approved", "released", "completed", "reopened"):
        raise HTTPException(404)
    o, note = load(ref), note.strip()
    ok = dict(sent=o.stage == "issued", approved=o.stage in ("issued", "sent") or (o.stage == "production" and o.approval_pending),
              released=o.stage in ("issued", "sent", "approved"), completed=o.stage in ("approved", "production"), reopened=o.stage == "completed")[kind]
    if not ok:
        raise HTTPException(400, "This step does not apply to the order as it stands now")
    if kind == "approved" and not note:
        raise HTTPException(400, "Enter who approved it at the client and how (for example: Mr Singh, by email)")
    if kind == "released" and o.stage != "approved":
        if user.role not in ("admin", "drawing"):
            raise HTTPException(403, "Only an administrator or a drawing user can release before the client has approved")
        if not note:
            raise HTTPException(400, "Enter the reason for releasing before client approval")
        note = "URGENT, before client approval: " + note
    with db.Session() as s:
        s.add(db.Step(order_id=o.id, kind=kind, note=note, by=user.name)); s.commit()
    return _after(request, ref)


@app.post("/orders/{ref}/remake/{no}")
def remake(ref: str, no: int, rid: int = Form(...), user=User):
    """Make this product again exactly as a past issued drawing of the same client."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or rid not in [p["id"] for p in o.past] or l.revisions:
        raise HTTPException(400, "Choose a past drawing of this client, on a product that has not been issued yet")
    with db.Session() as s:
        r = s.get(db.Revision, rid)
        for c in s.scalars(select(db.Change).where(db.Change.line_id == l.id)):
            if "remake_of" in (c.values or {}):
                c.status = "rejected"
        s.add(db.Change(line_id=l.id, typed="Product %d: make it the same as %s Rev %s" % (no, r.number, r.rev), understood="exact remake of %s Rev %s" % (r.number, r.rev),
                        values={"remake_of": rid}, status="confirmed", by=user.name))
        s.commit()
        o = load(ref)
        _draw_lines(o, s, user.name, only={no})
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


@app.post("/orders/{ref}/photo/{no}")
async def photo_upload(ref: str, no: int, file: UploadFile = File(...), user=User):
    """A photograph for this product. It is kept by product name, so the same product shows it on every order."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    data = await file.read(8 * 1024 * 1024 + 1)
    if l is None or not data or len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "Choose a photograph up to 8 MB")
    with db.Session() as s:
        if not _save_photo(s, l.name, data, user.name):
            raise HTTPException(400, "That file is not a photograph the desk can read (use JPG or PNG)")
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


@app.get("/photos/{pid}")
def photo(pid: int, user=User):
    with db.Session() as s:
        ph = s.get(db.Photo, pid)
        path = ph.path if ph else None
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/engine-requests")
def engine_request_new(text: str = Form(...), order_ref: str = Form(""), product: str = Form(""), user=User):
    """Any user may ask for an engine addition. Nothing changes until the administrator approves and the change is built and approved."""
    text = text.strip()
    if not text:
        raise HTTPException(400, "Describe what the engine should be able to draw")
    with db.Session() as s:
        s.add(db.EngineRequest(order_ref=order_ref[:80], product=product, text=text, asked_by=user.name))
        if order_ref:
            o = s.scalar(select(db.Order).where(db.Order.source_ref == order_ref))
            if o:
                s.add(db.Message(order_id=o.id, who="desk", text="Engine addition requested by %s%s: %s. It goes to the administrator for approval." % (user.name, " for " + product if product else "", text)))
        s.commit()
    return RedirectResponse(("/orders/%s/work" % order_ref) if order_ref else "/engine-requests", status_code=303)


@app.get("/engine-requests", response_class=HTMLResponse)
def engine_requests(request: Request, user=User):
    with db.Session() as s:
        rows = s.scalars(select(db.EngineRequest).order_by(db.EngineRequest.id.desc())).all()
    return T.TemplateResponse(request, "engine_requests.html", ctx(user, rows=rows))


@app.post("/engine-requests/{rid}/{decision}")
def engine_request_decide(rid: int, decision: str, note: str = Form(""), user=User):
    if user.role != "admin":
        raise HTTPException(403, "Only the administrator decides engine requests")
    new = dict(approve="approved", decline="declined", built="built").get(decision)
    with db.Session() as s:
        r = s.get(db.EngineRequest, rid)
        if r is None or new is None or (new == "built") != (r.status == "approved") or (new != "built" and r.status != "asked"):
            raise HTTPException(400, "This request cannot take that decision now")
        r.status, r.decided_by, r.decided_at, r.note = new, user.name, db.now(), (note.strip() or r.note)
        s.commit()
    return RedirectResponse("/engine-requests", status_code=303)


@app.post("/orders/{ref}/engine/{no}")
def move_engine(ref: str, no: int, user=User):
    """Redraw an issued product on the latest approved engines. It becomes a preview; issuing it makes a new revision."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not l.can_update_engine:
        raise HTTPException(400, "This product is already on the latest engines, or is not issued")
    v = engines.version()
    with db.Session() as s:
        s.add(db.Change(line_id=l.id, typed="Product %d: redraw on the latest engines (%s)" % (no, v), understood="redrawn on engines %s (was %s)" % (v, l.last_rev.engine_version),
                        values={"use_engine": v}, status="confirmed", by=user.name))
        s.commit()
        o = load(ref)
        _draw_lines(o, s, user.name, only={no})
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


@app.post("/orders/{ref}/sets/{kind}")
def make_sets(request: Request, ref: str, kind: str, job_order: str = Form(""), job_order_date: str = Form(""), deadline: str = Form(""), user=User):
    """Production (kind PRODUCTION) or subcontractor (VENDOR) drawings for every issued product, made from the issued revision
    on the engine version that revision was issued on. Only after the order is released to production."""
    kind = kind.upper()
    if kind not in ("PRODUCTION", "VENDOR"):
        raise HTTPException(404)
    o = load(ref)
    if o.stage not in ("production", "completed"):
        raise HTTPException(400, "Release the order to production first")
    details = dict(job_order=job_order.strip() or o.job_order or "TBC", job_order_date=job_order_date.strip() or "TBC", deadline=deadline.strip() or "TBC")
    made, failed = [], []
    ok = next((st for st in reversed(o.steps) if st.kind == "approved"), None)
    approved_on = "" if (o.approval_pending or ok is None) else ok.at.replace(tzinfo=ok.at.tzinfo or datetime.timezone.utc).astimezone(IST).strftime("%d %b %Y").upper()
    with db.Session() as s:
        if job_order.strip():
            s.get(db.Order, o.id).job_order = job_order.strip()[:80]
        for l in o.lines:
            r = l.last_rev
            if r is None:
                continue
            sp = __import__("copy").deepcopy(r.spec)
            sp["drawing_set"], sp["production"] = kind, dict(details)
            if approved_on:                                        # the client's approval, when it has been recorded for this issue
                sp["drawing"].update(approved="CLIENT", approval_date=approved_on)
            edir, ver = engines.dir_for(r.engine_version)
            try:
                files = engines.draw_set(sp, "%d/line%d/%s/rev%s" % (o.id, l.no, kind.lower(), r.rev), edir)
            except RuntimeError as e:
                failed.append("product %d (%s)" % (l.no, str(e)[:120])); continue
            for old in s.scalars(select(db.ProductionSet).where(db.ProductionSet.line_id == l.id, db.ProductionSet.kind == kind)):
                s.delete(old)
            s.add(db.ProductionSet(line_id=l.id, revision_id=r.id, kind=kind, files=files, details=details, engine_version=ver, made_by=user.name))
            made.append("%s Rev %s" % (r.number, r.rev))
        what = "Production drawings" if kind == "PRODUCTION" else "Subcontractor drawings"
        s.add(db.Message(order_id=o.id, who="desk", text=(what + " made for " + ", ".join(made) + "." if made else what + " could not be made.") + (" Not made: " + "; ".join(failed) + "." if failed else "")))
        s.commit()
    return _after(request, ref)


@app.post("/orders/{ref}/bom")
def make_bom(ref: str, panel_saw: str = Form(""), user=User):
    """The bill of material (Excel, with the cut list) of the whole order, from each product's latest issued revision,
    made by the approved BOM engine. Panel-saw cutting ticked: 4 mm saw cut, cut sizes, grain kept along the board,
    boards optimised (the engine's optional panel_saw rules)."""
    o = load(ref)
    if o.stage not in ("production", "completed"):
        raise HTTPException(400, "Release the order to production first")
    revs = {l.last_rev.number: l.last_rev.spec for l in o.lines if l.last_rev}
    if not revs:
        raise HTTPException(400, "Nothing has been issued yet")
    on = bool(panel_saw)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    pf = db.ProductionFile(order_id=o.id, kind="bom", panel_saw=on, made_by=user.name, engine_version=engines.version_label(),
                           revisions=", ".join(sorted(revs)), name="%s-Bill of Material%s.xlsx" % (o.source_ref, " (panel saw)" if on else ""))
    try:
        pf.path = engines.bom(revs, "%d/bom/%s-%s.xlsx" % (o.id, stamp, "panelsaw" if on else "standard"), panel_saw=on, client=o.client.name)
    except RuntimeError as e:
        pf.error = str(e)
    with db.Session() as s:
        s.add(pf)
        s.add(db.Message(order_id=o.id, who="desk", text=("Bill of material made%s by %s." % (" with panel-saw cutting" if on else "", user.name)) if not pf.error
                         else "The bill of material could not be made: %s" % pf.error))
        s.commit()
    return RedirectResponse("/orders/%s#sets" % ref, status_code=303)


@app.get("/production-files/{fid}")
def production_file(fid: int, user=User):
    with db.Session() as s:
        pf = s.get(db.ProductionFile, fid)
    if pf is None or pf.error or not os.path.exists(pf.path):
        raise HTTPException(404)
    return FileResponse(pf.path, filename=pf.name)


def _set_file(sid, i):
    with db.Session() as s:
        ps = s.get(db.ProductionSet, sid)
        if ps is None or not (0 <= i < len(ps.files)) or not os.path.exists(ps.files[i]["pdf"]):
            raise HTTPException(404)
        line = s.get(db.OrderLine, ps.line_id)
        order_ = s.get(db.Order, line.order_id)
        rev = s.get(db.Revision, ps.revision_id)
        return ps, ps.files[i], line, order_.source_ref, rev


@app.get("/sets/{sid}/{i}", response_class=HTMLResponse)
def set_view(request: Request, sid: int, i: int, user=User):
    ps, f, line, ref, rev = _set_file(sid, i)
    o = load(ref)
    return T.TemplateResponse(request, "set.html", ctx(user, o=o, tab="", ps=ps, f=f, i=i, line=line, rev=rev, kind="Production" if ps.kind == "PRODUCTION" else "Subcontractor"))


@app.get("/sets/{sid}/{i}/pdf")
def set_pdf(sid: int, i: int, download: int = 0, user=User):
    ps, f, line, ref, rev = _set_file(sid, i)
    return FileResponse(f["pdf"], filename=f.get("name") or "drawing.pdf", content_disposition_type="attachment" if download else "inline")


@app.get("/sets/{sid}/{i}/page/{n}")
def set_page(sid: int, i: int, n: int, user=User):
    ps, f, line, ref, rev = _set_file(sid, i)
    if not (0 <= n < f["pages"]):
        raise HTTPException(404)
    out = f["pdf"][:-4] + ".p%d.png" % n
    if not os.path.exists(out):
        import pymupdf
        with pymupdf.open(f["pdf"]) as doc:
            doc[n].get_pixmap(dpi=200).save(out)
    return FileResponse(out)


@app.post("/orders/{ref}/marks/{no}")
def mark_add(ref: str, no: int, shown: str = Form(...), x: float = Form(...), y: float = Form(...), label: str = Form(""), rev: int = Form(0), sheet: int = Form(1), user=User):
    """A numbered marker on a drawing. The number is then used when describing a change."""
    o = load(ref)
    l = next((z for z in o.lines if z.no == no), None)
    if l is None or not (0 <= x <= 100 and 0 <= y <= 100) or not re.fullmatch(r"[rd]\d+(s\d+)?", shown):
        raise HTTPException(400, "The marker could not be placed")
    with db.Session() as s:
        m = db.Mark(line_id=l.id, shown=shown, no=max([m.no for m in l.marks], default=0) + 1, x=round(x, 2), y=round(y, 2), label=label.strip()[:200], by=user.name)
        m.label = m.label or _mark_part(l, m)                         # no note typed: the part it sits on
        s.add(m)
        s.commit()
    return RedirectResponse(_view_url(ref, no, rev, sheet), status_code=303)


@app.post("/orders/{ref}/marks/{no}/{mid}/{what}")
def mark_edit(ref: str, no: int, mid: int, what: str, label: str = Form(""), rev: int = Form(0), sheet: int = Form(1), user=User):
    o = load(ref)
    l = next((z for z in o.lines if z.no == no), None)
    if l is None or what not in ("label", "remove") or mid not in [m.id for m in l.marks]:
        raise HTTPException(404)
    with db.Session() as s:
        m = s.get(db.Mark, mid)
        if what == "remove":
            s.delete(m)
        else:
            m.label = label.strip()[:200]
        s.commit()
    return RedirectResponse(_view_url(ref, no, rev, sheet), status_code=303)


# ---- choosing which quotation lines are drawn ------------------------------------------------------------------------
REMOVE_REASONS = ("Bought out after all", "Not needed by client", "Duplicate line", "Other")


def apply_no_drawing(ref):
    """The no-drawing rule (desk/nodrawing.py) on an order already in the desk, as it stands in Odoo now: bought-out items,
    chairs, services, installation and delivery lines move to "No drawing needed" with their product number kept (never
    reused). Not moved: a product with an issued drawing (that is a quotation change) or one added back on purpose.
    Odoo not reachable: the rules that go by the name still apply. -> (moved [(no, name, reason)], kept [(no, name, reason)])"""
    try:
        src = SOURCE.get_order(ref)
        why_by_name = {l.name: l.no_drawing for l in src.lines if l.no_drawing}
    except Exception:
        why_by_name = {}
    moved, kept = [], []
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        if o is None:
            return moved, kept
        for l in list(o.lines):
            why = why_by_name.get(l.name) or classify(l.name)
            if not why or l.keep:
                continue
            if l.revisions:
                kept.append((l.no, l.name, why)); continue
            o.not_drawn.append(db.NoDrawingLine(name=l.name, qty=l.qty, description=l.description, reason=why, no=l.no, by=""))
            moved.append((l.no, l.name, why))
            s.delete(l)
        if moved:
            o.messages.append(db.Message(who="desk", text="No drawing needed for %s, so %s moved to \"No drawing needed\". Product numbers are kept and not reused." % (
                "; ".join("Product %d (%s, %s)" % (n, nm.split("(")[0].strip(), w.lower()) for n, nm, w in moved), "it was" if len(moved) == 1 else "they were")))
        s.commit()
    return moved, kept


@app.post("/orders/{ref}/recheck")
def recheck(ref: str, user=User):
    """Check Odoo again: re-applies the no-drawing rule to this order."""
    load(ref)
    apply_no_drawing(ref)
    return RedirectResponse("/orders/%s" % ref, status_code=303)


def _next_no(s, order_id):
    """The next product number: after every number used so far, including removed ones (numbers are never reused)."""
    used = [l.no for l in s.scalars(select(db.OrderLine).where(db.OrderLine.order_id == order_id))]
    used += [x.no for x in s.scalars(select(db.NoDrawingLine).where(db.NoDrawingLine.order_id == order_id))]
    return max(used + [0]) + 1


def _new_line(s, o_id, no, name, description, qty):
    line = db.OrderLine(order_id=o_id, no=no, name=name, description=description, qty=qty)
    for q in read_line(name, description)["questions"]:
        line.questions.append(db.Question(key=q["key"], text=q["text"], options=q["options"]))
    s.add(line)
    return line


@app.post("/orders/{ref}/lines/{no}/remove")
def remove_line(ref: str, no: int, reason: str = Form(""), user=User):
    """Takes one product off the drawings. It moves to "No drawing needed"; nothing is deleted and it can be added back.
    A product whose drawing was issued is not removed here: that is a quotation change (cancelled stamp, reissue)."""
    reason = reason if reason in REMOVE_REASONS else "Other"
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        l = o and next((x for x in o.lines if x.no == no), None)
        if l is None:
            raise HTTPException(404, "Product %d was not found" % no)
        if l.revisions:
            raise HTTPException(400, "Product %d has an issued drawing. Removing it after issue is a quotation change: it gets a Cancelled stamp and the set is reissued." % no)
        o.not_drawn.append(db.NoDrawingLine(name=l.name, qty=l.qty, description=l.description, reason="Removed: " + reason, no=l.no, by=user.name))
        o.messages.append(db.Message(who="desk", text="Product %d (%s) was removed from the drawings by %s. Reason: %s. It can be added back." % (no, l.name.split("(")[0].strip(), user.name, reason)))
        s.delete(l); s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/not-drawn/{nid}/add")
def add_back(ref: str, nid: int, user=User):
    """Puts a line from "No drawing needed" on the drawings. A removed product gets its old number back."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        x = o and next((y for y in o.not_drawn if y.id == nid), None)
        if x is None:
            raise HTTPException(404, "That line was not found")
        no = x.no or _next_no(s, o.id)
        name, description, qty = x.name, x.description, x.qty
        s.delete(x); s.flush()
        _new_line(s, o.id, no, name, description, qty).keep = True
        s.add(db.Message(order_id=o.id, who="desk", text="%s was added to the drawings as Product %d by %s." % (name.split("(")[0].strip(), no, user.name)))
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/lines/add")
def add_line(ref: str, name: str = Form(""), qty: float = Form(1), details: str = Form(""), user=User):
    """Adds a product that is not in the quotation. It gets the next number, and the salesperson is told to update the quotation."""
    name = " ".join(name.split())[:200]
    if not name or qty <= 0:
        raise HTTPException(400, "Give the product name and a quantity above 0")
    load(ref)
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        no = _next_no(s, o.id)
        _new_line(s, o.id, no, name, details.strip(), qty)
        s.add(db.AddedLine(order_id=o.id, no=no, name=name, by=user.name))
        s.add(db.Message(order_id=o.id, who="desk", text="Product %d (%s, qty %g) was added by %s. It is not in the quotation: the salesperson must add it to the quotation." % (no, name, qty, user.name)))
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/remove")
def remove_order(ref: str, user=User):
    """Takes an order off the desk. Only the administrator, and only while nothing has been issued. Odoo is not touched;
    the order can be opened again later."""
    if user.role != "admin":
        raise HTTPException(403, "Only the administrator can remove a project")
    o = load(ref)
    if any(l.revisions for l in o.lines):
        raise HTTPException(400, "This project has issued drawings and cannot be removed. Mark it completed instead.")
    with db.Session() as s:
        s.delete(s.get(db.Order, o.id)); s.commit()
    return RedirectResponse("/", status_code=303)
