"""Encore drawing desk: dashboard, orders, questions, previews, changes, issue with numbers and revisions, the steps after
issue, engine requests, product photographs and remakes. Not built yet: Odoo source, client portal, cloud archive."""
import os
from fastapi import FastAPI, Form, HTTPException, Request, Depends, UploadFile, File
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from . import db, spec, engines, finishes, auth, changes as chg
import datetime, secrets, io, re
from .reading import read_line, fmt
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
        if not l.product_ref or s.scalar(select(db.Photo).where(db.Photo.product == product_key(l.name))):
            continue
        try:
            data = source.product_image(l.product_ref)
        except Exception:
            continue                                               # a missing photograph never stops an order from opening
        if data:
            _save_photo(s, l.name, data, "Odoo")


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
                line = db.OrderLine(no=l.no, name=l.name, description=l.description, qty=l.qty)
                for q in read_line(l.name, l.description)["questions"]:
                    line.questions.append(db.Question(key=q["key"], text=q["text"], options=q["options"])); total += 1
                o.lines.append(line)
            n = len(src.lines)
            o.messages.append(db.Message(who="desk", text="I have read the %d product%s of this order. %s" % (n, "" if n == 1 else "s",
                              "%d thing%s need%s your answer before I draw." % (total, "" if total == 1 else "s", "s" if total == 1 else "") if total else "Nothing is missing; the drawings can be made.")))
            s.add(o); s.commit()
            _fetch_photos(s, src)
        s.refresh(o)
        for l in o.lines:
            l.read = read_line(l.name, l.description)
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
            _ = l.sets, l.marks
            if l.remake:
                l.open_count = 0                                     # an exact copy of a past drawing needs no answers
            ph = s.scalar(select(db.Photo).where(db.Photo.product == product_key(l.name)))
            l.photo = ph.id if ph else None
            l.photo_v = int(ph.at.timestamp()) if ph else 0
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
                rows.append(dict(ref=o.source_ref, client=o.client.name, n=n, issued=issued, failed=failed, stage=st, stage_text=STAGES[st], pending=pend and st == "production",
                                 salesperson=o.salesperson, last=last, drawings=[dict(number=l.revisions[-1].number, rev=l.revisions[-1].rev, no=l.no) for l in o.lines if l.revisions]))
        known = {o.source_ref for o in s.scalars(select(db.Order))}
    samples = [r for r in getattr(SOURCE, "list_refs", lambda: [])() if r not in known]
    odoo = (ODOO.status() if ODOO.configured else "Not connected yet.") if user.role == "admin" else ""
    return T.TemplateResponse(request, "dashboard.html", ctx(user, rows=rows, count=count, show=show, samples=samples, odoo=odoo, odoo_on=ODOO.configured, focus=focus, items=items, focus_title=titles.get(focus, ""),
                                                                  hide_back=not focus and show == "active"))


@app.post("/open")
def open_order(ref: str = Form(...), user=User):
    return RedirectResponse("/orders/%s" % ref.strip(), status_code=303)


def _ready(l):
    return bool(l.drawing and l.drawing.status == "preview" and not l.open_count and not any(c.status == "asked" for c in l.changes))


def _after(request, ref):
    """Back to the page the action was taken on: the project page or the drawing conversation."""
    from urllib.parse import urlparse
    came = urlparse(request.headers.get("referer", "")).path
    return RedirectResponse("/orders/%s" % ref if came == "/orders/%s" % ref else "/orders/%s/work" % ref, status_code=303)


FLOW = [("drawing", "Drawings"), ("issued", "Issued"), ("sent", "With client"), ("approved", "Approved"), ("production", "In production"), ("completed", "Completed")]


@app.get("/orders/{ref}", response_class=HTMLResponse)
def project(request: Request, ref: str, user=User):
    """The project page: where the order stands, what needs attention, and every product with its drawing."""
    o = load(ref)
    todo = []
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
    return T.TemplateResponse(request, "project.html", ctx(user, o=o, todo=todo, flow=flow, latest=engines.version(), issued=sum(1 for l in o.lines if l.revisions),
                              prod_sets=[x for l in o.lines for x in l.sets if x.kind == "PRODUCTION"], vendor_sets=[x for l in o.lines for x in l.sets if x.kind == "VENDOR"],
                              today=datetime.date.today().strftime("%d %b %Y").upper()))


@app.get("/orders/{ref}/work", response_class=HTMLResponse)
def order(request: Request, ref: str, user=User):
    o = load(ref)
    return T.TemplateResponse(request, "order.html", ctx(user, o=o, events=timeline(o), open_total=sum(l.open_count for l in o.lines),
                              can_draw=any(l.open_count == 0 for l in o.lines), drawn=sum(1 for l in o.lines if l.drawing and l.drawing.status == "preview"),
                              can_issue=True, not_ready=[l.no for l in o.lines if not _ready(l)],
                              needs_issue=any(_ready(l) and not l.issued_current for l in o.lines), latest=engines.version(),
                              prod_sets=[x for l in o.lines for x in l.sets if x.kind == "PRODUCTION"], vendor_sets=[x for l in o.lines for x in l.sets if x.kind == "VENDOR"],
                              today=datetime.date.today().strftime("%d %b %Y").upper()))


@app.post("/orders/{ref}/answer/{qid}")
def answer(ref: str, qid: int, value: str = Form(...), user=User):
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
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


def make_spec(s, o, l, issue=None, edir=None):
    """The specification for one product: an exact copy of a past drawing when it is a remake, otherwise built from the order."""
    if l.remake:
        past = s.get(db.Revision, l.remake)
        if past is None:
            raise spec.CannotDraw("The past drawing chosen for this remake was not found.")
        return spec.remake(o, l, past.spec, l.no, issue=issue)
    return spec.build(edir or engines.ENGINES_DIR, o, l, l.read, {q.key: q.answer for q in l.questions}, l.no, changes=l.confirmed,
                      finish=lambda text, kind: finishes.lookup(s, text, kind), issue=issue)


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
    s.commit()


@app.post("/orders/{ref}/draw")
def draw(ref: str, user=User):
    o = load(ref)
    with db.Session() as s:
        _draw_lines(o, s, user.name)
    return RedirectResponse("/orders/%s/work" % ref, status_code=303)


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
            typed = text + (" [Markers: %s]" % "; ".join("%d = %s" % (m.no, m.label or "marked on the drawing") for m in used) if used else "")
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


def _big(pdf_path):
    """A sharp picture of the sheet for viewing inside the desk, made from the PDF once and kept beside it."""
    out = pdf_path[:-4] + ".view.png"
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(pdf_path):
        import pymupdf
        with pymupdf.open(pdf_path) as doc:
            doc[0].get_pixmap(dpi=220).save(out)
    return out


@app.get("/revisions/{rid}/{kind}")
def revision_file(rid: int, kind: str, download: int = 0, user=User):
    if kind not in ("png", "pdf", "dxf", "big"):
        raise HTTPException(404)
    with db.Session() as s:
        r = s.get(db.Revision, rid)
        path, name = (r.files.get("pdf" if kind == "big" else kind), "%s-Rev%s.%s" % (r.number, r.rev, kind)) if r else (None, "")
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    if kind == "big":
        return FileResponse(_big(path))
    return FileResponse(path, filename=None if kind == "png" else name, content_disposition_type="attachment" if download or kind == "dxf" else "inline")


@app.get("/drawings/{did}/{kind}")
def drawing_file(did: int, kind: str, download: int = 0, user=User):
    if kind not in ("png", "pdf", "dxf", "big"):
        raise HTTPException(404)
    with db.Session() as s:
        d = s.get(db.Drawing, did)
        path = d.files.get("pdf" if kind == "big" else kind) if d else None
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    if kind == "big":
        return FileResponse(_big(path))
    return FileResponse(path, filename=None if kind == "png" else "PREVIEW-drawing-%d.%s" % (did, kind), content_disposition_type="attachment" if download or kind == "dxf" else "inline")


@app.get("/orders/{ref}/view/{no}", response_class=HTMLResponse)
def view(request: Request, ref: str, no: int, rev: int = 0, user=User):
    """One product's drawing, full size, inside the desk: the current preview or any issued revision, with its change box."""
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None or not (l.drawing or l.revisions):
        raise HTTPException(404)
    r = next((x for x in l.revisions if x.id == rev), None)
    if r:
        src, title = "/revisions/%d" % r.id, "%s Rev %s" % (r.number, r.rev)
    elif l.drawing and l.drawing.status == "preview":
        current = l.issued_current
        src, title = ("/revisions/%d" % l.last_rev.id, "%s Rev %s" % (l.last_rev.number, l.last_rev.rev)) if current else ("/drawings/%d" % l.drawing.id, "Preview, not issued")
        r = l.last_rev if current else None
    elif l.last_rev:
        r = l.last_rev
        src, title = "/revisions/%d" % r.id, "%s Rev %s" % (r.number, r.rev)
    else:
        raise HTTPException(404)
    stamp = int((r.issued_at if r else l.drawing.made_at).timestamp())
    key = "r%d" % r.id if r else "d%d" % stamp
    return T.TemplateResponse(request, "view.html", ctx(user, o=o, l=l, src=src, title=title, shown=r, stamp=stamp, key=key, rev_q="?rev=%d" % r.id if rev else "",
                              marks=[m for m in l.marks if m.shown == key], next_no=max([m.no for m in l.marks], default=0) + 1))


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
    return T.TemplateResponse(request, "set.html", ctx(user, o=o, ps=ps, f=f, i=i, line=line, rev=rev, kind="Production" if ps.kind == "PRODUCTION" else "Subcontractor"))


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
def mark_add(ref: str, no: int, shown: str = Form(...), x: float = Form(...), y: float = Form(...), label: str = Form(""), rev: int = Form(0), user=User):
    """A numbered marker on a drawing. The number is then used when describing a change."""
    o = load(ref)
    l = next((z for z in o.lines if z.no == no), None)
    if l is None or not (0 <= x <= 100 and 0 <= y <= 100) or not re.fullmatch(r"[rd]\d+", shown):
        raise HTTPException(400, "The marker could not be placed")
    with db.Session() as s:
        s.add(db.Mark(line_id=l.id, shown=shown, no=max([m.no for m in l.marks], default=0) + 1, x=round(x, 2), y=round(y, 2), label=label.strip()[:200], by=user.name))
        s.commit()
    return RedirectResponse("/orders/%s/view/%d%s" % (ref, no, "?rev=%d" % rev if rev else ""), status_code=303)


@app.post("/orders/{ref}/marks/{no}/{mid}/{what}")
def mark_edit(ref: str, no: int, mid: int, what: str, label: str = Form(""), rev: int = Form(0), user=User):
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
    return RedirectResponse("/orders/%s/view/%d%s" % (ref, no, "?rev=%d" % rev if rev else ""), status_code=303)
