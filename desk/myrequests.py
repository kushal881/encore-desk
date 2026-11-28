"""Screen 7a: My requests. Everything the signed-in person asked an administrator for (engine changes, changes after client
approval, revision raises), the decision and the task it made on that drawing. Summary tiles Pending / Approved / Rejected
/ Done.
- Approved, option B (sent to engine work, built) and revision raises: "Apply & regenerate" redraws the product (on the
  newest approved engines when the request was engine work) so it goes back to Review, and closes the task.
- Approved, option A (this client only) and changes after approval: the change is made in the product's change panel, so
  the button opens it; "Mark task done" closes the task once it is made.
- Rejected: the reason is shown; "Mark task done" closes the task.
Routes are kept here, apart from app.py; app.py includes this router."""
import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db, engines
from .app import T, User, ctx, load, _draw_lines

router = APIRouter()
KIND = dict(engine="Engine change", after_approval="Change after client approval", revision="Revision raise")


def _status(req_status, task):
    if req_status == "asked":
        return "Pending"
    if task is not None and task.status == "done":
        return "Done"
    return "Rejected" if req_status in ("declined", "rejected") else "Approved"


def requests_of(s, name):
    """-> [dict] for every request this person asked, newest first."""
    tasks = {t.source: t for t in s.scalars(select(db.Task).where(db.Task.assignee == name).order_by(db.Task.id))}
    out = []
    for r in s.scalars(select(db.EngineRequest).where(db.EngineRequest.asked_by == name)):
        t = tasks.get("engine:%d" % r.id)
        no = int((re.search(r"Product (\d+)", r.product or "") or [0, 0])[1])
        out.append(dict(kind="engine", id=r.id, title=r.text, ref=r.order_ref, no=no, product=r.product, asked=r.asked_at, req=r.status,
                        option=(getattr(r, "option", "") or {"engine_work": "B", "built": "B", "approved": "A"}.get(r.status, "")), decided_by=r.decided_by, note=r.note, task=t))
    for r in s.scalars(select(db.ChangeRequest).where(db.ChangeRequest.asked_by == name)):
        t = tasks.get("change:%d" % r.id)
        out.append(dict(kind=r.kind, id=r.id, title=r.text, ref=r.order_ref, no=r.line_no, product=r.product, asked=r.asked_at, req=r.status,
                        option="", decided_by=r.decided_by, note=r.note, task=t))
    for d in out:
        d["status"] = _status(d["req"], d["task"])
        d["kind_text"] = KIND.get(d["kind"], d["kind"])
        d["action"] = _action(d)
    return sorted(out, key=lambda d: d["asked"].replace(tzinfo=None), reverse=True)


def _action(d):
    """-> (label, kind of action) for the one button on a request, or None. kind: apply | panel | done."""
    if d["status"] == "Approved":
        if (d["kind"] == "engine" and d["option"] == "B") or d["kind"] == "revision":
            return ("Apply & regenerate", "apply")
        return ("Open the change panel", "panel")
    if d["status"] == "Rejected" and d["task"] is not None:
        return ("Mark task done", "done")
    return None


@router.get("/my-requests", response_class=HTMLResponse)
def my_requests(request: Request, show: str = "All", user=User):
    show = show if show in ("All", "Pending", "Approved", "Rejected", "Done") else "All"
    with db.Session() as s:
        rows = requests_of(s, user.name)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("Pending", "Approved", "Rejected", "Done")}
    shown = [r for r in rows if show == "All" or r["status"] == show]
    return T.TemplateResponse(request, "myrequests.html", ctx(user, nav="mine", rows=shown, counts=counts, show=show,
                                                               title="All my requests" if show == "All" else show + " requests"))


@router.post("/my-requests/{kind}/{rid}/apply")
def apply(kind: str, rid: int, user=User):
    """Apply & regenerate an approved request: redraw the product, which goes back to Review, and close the task."""
    with db.Session() as s:
        d = next((x for x in requests_of(s, user.name) if x["kind"] == kind and x["id"] == rid), None)
    if d is None:
        raise HTTPException(404, "This is not one of your requests")
    if d["action"] is None or d["action"][1] != "apply":
        raise HTTPException(400, "This request has no change to apply now")
    o = load(d["ref"])
    l = next((x for x in o.lines if x.no == d["no"]), None)
    if l is None:
        raise HTTPException(400, "Product %d is no longer in %s" % (d["no"], d["ref"]))
    if l.open_count:
        raise HTTPException(400, "Product %d has questions to answer first" % l.no)
    if getattr(l, "ai_waiting", False):
        raise HTTPException(400, "Product %d: Accept or Undo the change in the change panel first" % l.no)
    with db.Session() as s:
        latest = engines.version()
        if d["kind"] == "engine" and l.last_rev and l.engine_for != latest:    # an issued product stays on its version unless moved
            s.add(db.Change(line_id=l.id, typed="Product %d: redraw on the latest engines (%s) for the approved engine change" % (l.no, latest),
                            understood="redrawn on engines %s" % latest, values={"use_engine": latest}, status="confirmed", by=user.name))
            s.commit()
            o = load(d["ref"])
        _draw_lines(o, s, user.name, only={l.no})
        t = s.get(db.Task, d["task"].id) if d["task"] else None
        if t is not None:
            t.status, t.done_at = "done", db.now()
        o_ = s.get(db.Order, o.id)
        s.add(db.Message(order_id=o_.id, who="desk", text="%s applied by %s: Product %d redrawn for review (%s)." % (KIND[d["kind"]], user.name, l.no, d["title"][:200])))
        s.commit()
    return RedirectResponse("/orders/%s/view/%d" % (d["ref"], l.no), status_code=303)
