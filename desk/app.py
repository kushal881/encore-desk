"""Encore drawing desk. Built so far: open an order, show what was read, ask what is missing, record the answers,
run the approved engine and show previews. Not built yet: sign-in, change requests, issue, Odoo source."""
import os
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from . import db, spec, engines
from .reading import read_line, fmt
from .sources.sample import SampleSource

app = FastAPI(title="Encore drawing desk")
T = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
SOURCE = SampleSource()                       # the Odoo source replaces this line when it is connected
USER = os.environ.get("DESK_USER", "Kushal Singhal")   # until sign-in is built
db.init()


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
            _ = l.questions, l.drawing
        _ = o.client, o.messages
        return o


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/orders/Q-2026-10-444")


@app.get("/orders/{ref}", response_class=HTMLResponse)
def order(request: Request, ref: str):
    o = load(ref)
    return T.TemplateResponse(request, "order.html", dict(o=o, user=USER, open_total=sum(l.open_count for l in o.lines), engine_version=engines.version(),
                              can_draw=any(l.open_count == 0 for l in o.lines), drawn=sum(1 for l in o.lines if l.drawing and l.drawing.status == "preview")))


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


@app.post("/orders/{ref}/draw")
def draw(ref: str):
    """Runs the approved engine for every line whose questions are all answered. Each result is a preview, never an issued drawing."""
    o = load(ref)
    with db.Session() as s:
        for l in o.lines:
            if l.open_count:
                continue
            line = s.get(db.OrderLine, l.id)
            d = line.drawing or db.Drawing()
            line.drawing = d
            d.engine_version, d.made_by, d.made_at, d.error, d.files = engines.version(), USER, db.now(), "", {}
            try:
                d.spec = spec.build(engines.ENGINES_DIR, o, l, l.read, {q.key: q.answer for q in l.questions}, l.no)
                d.files = engines.draw(d.spec, "%d/line%d" % (o.id, l.no))
                d.status = "preview"
            except (spec.CannotDraw, RuntimeError) as e:
                d.status, d.error = "failed", str(e)
        s.commit()
    return RedirectResponse("/orders/%s" % ref, status_code=303)


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
