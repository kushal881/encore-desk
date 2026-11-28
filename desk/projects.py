"""Screen 3a: Projects. Every drawing project with its stage (1 Order ... 8 Done), drawings done / total, what needs
attention and the last activity. Stage tiles filter, Active / Completed / All tabs, salesperson filter and search.
Read only: nothing here changes a project. Routes are kept here, apart from app.py; app.py includes this router."""
import datetime
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from . import db
from .app import T, User, ctx, stage_of
from .viewer import STEPS

router = APIRouter()


def project_stage(st, lines):
    """The project's step 1..8 from the order's stage and its products.
    Before issue: 1 Order (no product to draw yet), 2 Specify (questions open), 3 Generate (answered, not drawn),
    4 Review (previews made). After issue: 5 Issue (to be sent), 6 Client approval, 7 Production (approved or released), 8 Done."""
    after = dict(issued=5, sent=6, approved=7, production=7, completed=8)
    if st in after:
        return after[st]
    if not lines:
        return 1
    if any(not q.answer for l in lines for q in l.questions) and not any(l.drawing for l in lines):
        return 2
    if any(l.drawing is None for l in lines):
        return 2 if any(not q.answer for l in lines for q in l.questions) else 3
    return 4


def attention(s, o, st, stage, lines, today=None):
    """The one thing that needs someone on this project, in a few words, or ''. Most urgent first."""
    today = today or datetime.datetime.now(datetime.timezone.utc)
    if s.scalar(select(db.EngineRequest.id).where(db.EngineRequest.order_ref == o.source_ref, db.EngineRequest.status == "asked").limit(1)):
        return "Engine request pending"
    if s.scalar(select(db.ChangeRequest.id).where(db.ChangeRequest.order_ref == o.source_ref, db.ChangeRequest.status == "asked").limit(1)):
        return "Waiting for an admin decision"
    failed = sum(1 for l in lines if l.drawing and l.drawing.status == "failed")
    if failed:
        return "%d could not be drawn" % failed
    asked = sum(1 for l in lines for c in l.changes if c.status == "asked")
    if asked:
        return "%d change request%s to confirm" % (asked, "" if asked == 1 else "s")
    ids = [l.id for l in lines]
    if ids and s.scalar(select(db.AiTurn.id).where(db.AiTurn.line_id.in_(ids), db.AiTurn.status == "trial").limit(1)):
        return "AI change waiting: Accept or Undo"
    latest = {l.revisions[-1].id: l for l in lines if l.revisions}
    if latest:
        last_answer = {}
        for a in s.scalars(select(db.Approval).where(db.Approval.order_id == o.id).order_by(db.Approval.id)):
            last_answer[a.revision_id] = a.outcome
        if any(last_answer.get(rid) == "changes" for rid in latest):
            return "Client asked for changes"
    q = sum(1 for l in lines for x in l.questions if not x.answer)
    if q and stage <= 3:
        return "%d question%s to answer" % (q, "" if q == 1 else "s")
    if st == "sent":
        sent = [x.at for x in o.steps if x.kind == "sent"]
        if sent:
            days = (today.replace(tzinfo=None) - sent[-1].replace(tzinfo=None)).days
            if days >= 2:
                return "Waiting %d days on client" % days
    if st == "production" and not any(x.kind == "PRODUCTION" for l in lines for x in l.sets):
        return "Production set to make"
    if st == "issued":
        return "Send the drawings to the client"
    return ""


def folder(o):
    """The client's server folder for this project: Encore-Nov-2026-05, or '' before a folder number is known."""
    if not o.client.folder_no:
        return ""
    d = o.read_at or datetime.datetime.now(datetime.timezone.utc)
    return "Encore-%s-%d-%s" % (d.strftime("%b"), d.year, o.client.folder_no)


def rows(s):
    out = []
    for o in s.scalars(select(db.Order).order_by(db.Order.id.desc())):
        lines = list(o.lines)
        st, _ = stage_of(o.steps, any(l.revisions for l in lines))
        n = project_stage(st, lines)
        last = max([o.read_at] + [x.at for x in o.steps] + [r.issued_at for l in lines for r in l.revisions]
                   + [m.at for m in o.messages] + [q.answered_at for l in lines for q in l.questions if q.answered_at],
                   key=lambda d: d.replace(tzinfo=None))
        out.append(dict(ref=o.source_ref, client=o.client.name, folder=folder(o), salesperson=o.salesperson or "", st=n, stage="%d · %s" % (n, STEPS[n - 1]),
                        done=sum(1 for l in lines if l.revisions), total=len(lines), flag=attention(s, o, st, n, lines), last=last))
    return out


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request, show: str = "Active", stage: int = 0, sp: str = "", q: str = "", user=User):
    show = show if show in ("Active", "Completed", "All") else "Active"
    stage = stage if 1 <= stage <= 8 else 0
    with db.Session() as s:
        allr = rows(s)
    counts = [sum(1 for r in allr if r["st"] == i + 1) for i in range(8)]
    people = sorted({r["salesperson"] for r in allr if r["salesperson"]})
    ql = q.strip().lower()
    shown = [r for r in allr if (show == "All" or (r["st"] == 8) == (show == "Completed")) and (not stage or r["st"] == stage)
             and (not sp or r["salesperson"] == sp) and (not ql or ql in (r["client"] + " " + r["ref"] + " " + r["folder"]).lower())]
    title = (STEPS[stage - 1] + " · " if stage else "") + show + " projects"
    return T.TemplateResponse(request, "projects.html", ctx(user, nav="projects", rows=shown, counts=counts, steps=STEPS, show=show, stage=stage,
                                                             sp=sp, q=q, people=people, title=title))
