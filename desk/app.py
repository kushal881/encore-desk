"""Encore drawing desk. Built so far: open an order, show what was read, ask what is missing, record the answers,
run the approved engine and show previews. Not built yet: sign-in, change requests, issue, Odoo source."""
import os
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from . import db, spec, engines, finishes, changes as chg
import datetime
from .reading import read_line, fmt
from .sources.sample import SampleSource

app = FastAPI(title="Encore drawing desk")
T = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
SOURCE = SampleSource()                       # the Odoo source replaces this line when it is connected
USER = os.environ.get("DESK_USER", "Kushal Singhal")   # until sign-in is built
db.init()
with db.Session() as _s:
    finishes.seed(_s)


def load(ref):
    """The order from our own database; read from its source and copied in the first time it is opened."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        if o is None:
            try:
                src = SOURCE.get_order(ref)
            except KeyError:
                raise HTTPException(404, "Order %s was not found" % ref)
            c = s.scalar(select(db.Client).where(db.Client.name == src.client_name)) or db.Client(name=src.client_name, contact=src.client_contact)
            o = db.Order(client=c, source=src.source, source_ref=src.ref, job_order=src.job_order, salesperson=src.salesperson, delivery=src.delivery)
            total = 0
            for l in src.lines:
                line = db.OrderLine(no=l.no, name=l.name, description=l.description, qty=l.qty)
                for q in read_line(l.name, l.description)["questions"]:
                    line.questions.append(db.Question(key=q["key"], text=q["text"], options=q["options"])); total += 1
                o.lines.append(line)
            n = len(src.lines)
            o.messages.append(db.Message(who="desk", text="I have read the %d line%s of this order. %s" % (n, "" if n == 1 else "s",
                              "%d thing%s need%s your answer before I draw." % (total, "" if total == 1 else "s", "s" if total == 1 else "") if total else "Nothing is missing; the drawings can be made.")))
            s.add(o); s.commit()
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
        _ = o.client, o.messages
        return o


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/orders/Q-2026-10-444")


@app.get("/orders/{ref}", response_class=HTMLResponse)
def order(request: Request, ref: str):
    o = load(ref)
    return T.TemplateResponse(request, "order.html", dict(o=o, user=USER, open_total=sum(l.open_count for l in o.lines), engine_version=engines.version(),
                              can_draw=any(l.open_count == 0 for l in o.lines), drawn=sum(1 for l in o.lines if l.drawing and l.drawing.status == "preview"),
                              can_issue=all(l.drawing and l.drawing.status == "preview" and not l.open_count and not any(c.status == "asked" for c in l.changes) for l in o.lines),
                              needs_issue=any(not l.last_rev or (l.drawing and l.last_rev.spec.get("model") != l.drawing.spec.get("model")) for l in o.lines)))


@app.post("/orders/{ref}/answer/{qid}")
def answer(ref: str, qid: int, value: str = Form(...)):
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
        q.answer, q.answered_by, q.answered_at = value, USER, db.now()
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


def _draw_lines(o, s, only=None):
    """Runs the approved engine for every line whose questions are all answered. Each result is a preview."""
    for l in o.lines:
        if l.open_count or (only and l.no not in only):
            continue
        line = s.get(db.OrderLine, l.id)
        d = line.drawing or db.Drawing()
        line.drawing = d
        d.engine_version, d.made_by, d.made_at, d.error, d.files = engines.version(), USER, db.now(), "", {}
        try:
            d.spec = spec.build(engines.ENGINES_DIR, o, l, l.read, {q.key: q.answer for q in l.questions}, l.no, changes=l.confirmed,
                                finish=lambda text, kind: finishes.lookup(s, text, kind))
            d.files = engines.draw(d.spec, "%d/line%d/preview" % (o.id, l.no))
            d.status = "preview"
        except (spec.CannotDraw, RuntimeError) as e:
            d.status, d.error = "failed", str(e)
    s.commit()


@app.post("/orders/{ref}/draw")
def draw(ref: str):
    o = load(ref)
    with db.Session() as s:
        _draw_lines(o, s)
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/change")
def change(ref: str, text: str = Form(...)):
    """A typed change request. It is read, said back, and applied only after the user confirms."""
    text = text.strip()
    if not text:
        raise HTTPException(400, "Type the change first")
    o = load(ref)
    lines = [(l.no, l.read["family"]) for l in o.lines]
    with db.Session() as s:
        no = chg.which_line(text, lines)
        if no is None:
            s.add(db.Message(order_id=o.id, who=USER, text=text))
            s.add(db.Message(order_id=o.id, who="desk", text="Which line is this for? Start with the line number, for example: Line 2: " + text))
        else:
            l = next(x for x in o.lines if x.no == no)
            values, said = chg.understand(text, l.read["family"])
            s.add(db.Change(line_id=l.id, typed=text, understood=said, values=values or {}, status="asked" if values else "cannot", by=USER))
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/change/{cid}/{decision}")
def decide(ref: str, cid: int, decision: str):
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
            _draw_lines(o, s, only={line.no})
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.post("/orders/{ref}/issue")
def issue(ref: str, folder_no: str = Form("")):
    """Issues every previewed line whose specification differs from its last issued revision. An issued revision is never changed."""
    o = load(ref)
    ready = [l for l in o.lines if l.drawing and l.drawing.status == "preview" and not l.open_count and not any(c.status == "asked" for c in l.changes)]
    if not ready or len(ready) != len(o.lines):
        raise HTTPException(400, "Every line needs a preview and no unanswered question or unconfirmed change before issue")
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
            if l.last_rev and l.last_rev.spec.get("model") == model_now and l.last_rev.spec.get("swatches") == l.drawing.spec.get("swatches"):
                continue                                             # nothing changed since the last issue
            if l.last_rev:
                serial = l.last_rev.number.rsplit("-", 1)[1]
                rev = chr(ord(l.last_rev.rev) + 1)
                new = [c for c in l.changes if c.status == "confirmed" and c.at > l.last_rev.issued_at]
                reason = "; ".join(c.understood for c in new) or "Reissued"
                short = ("; ".join(chg.compact(c.values) for c in new) or "Reissued")
                revs = l.last_rev.spec["revisions"] + [dict(rev=rev, date=today, desc=short if len(short) <= 40 else short[:37] + "...", by=USER.split()[0])]
            else:
                serial, nxt = "%02d" % nxt, nxt + 1
                rev, reason = "A", "Initial issue for client approval"
                revs = [dict(rev="A", date=today, desc=reason, by="")]
            sp = spec.build(engines.ENGINES_DIR, o, l, l.read, answers, l.no, changes=l.confirmed, finish=lambda t, k: finishes.lookup(s, t, k),
                            issue=dict(folder=client.folder_no, serial=serial, revisions=revs))
            files = engines.draw(sp, "%d/line%d/issued/rev%s" % (o.id, l.no, rev))
            number = "Encore-%s-%s-%s-%s" % (datetime.datetime.strptime(revs[0]["date"], "%d %b %Y").strftime("%b"), revs[0]["date"][-4:], client.folder_no, serial)
            s.add(db.Revision(line_id=l.id, number=number, rev=rev, reason=reason, engine_version=engines.version(), spec=sp, files=files, issued_by=USER))
            done.append("%s Rev %s" % (number, rev))
        s.add(db.Message(order_id=o.id, who="desk", text=("Issued: " + ", ".join(done) + ". Filing to Odoo and the cloud archive is not connected yet; the files are kept by the desk.") if done
                         else "Nothing to issue: no line has changed since its last issued revision."))
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


@app.get("/revisions/{rid}/{kind}")
def revision_file(rid: int, kind: str):
    if kind not in ("png", "pdf", "dxf"):
        raise HTTPException(404)
    with db.Session() as s:
        r = s.get(db.Revision, rid)
        path, name = (r.files.get(kind), "%s-Rev%s.%s" % (r.number, r.rev, kind)) if r else (None, "")
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=None if kind == "png" else name)


@app.get("/drawings/{did}/{kind}")
def drawing_file(did: int, kind: str):
    if kind not in ("png", "pdf", "dxf"):
        raise HTTPException(404)
    with db.Session() as s:
        d = s.get(db.Drawing, did)
        path = d.files.get(kind) if d else None
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=None if kind == "png" else "PREVIEW-drawing-%d.%s" % (did, kind))
