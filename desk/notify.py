"""Notifications (issue #48): a bell on every desk page with the signed-in person's open notifications, a panel and a
Notifications page (Open / Resolved, by project). A notification is never removed until the thing it points at is
resolved; opening it only marks it seen. Notes that need no action (the client approved a drawing) are resolved when opened.

Rather than every part of the desk sending notifications, the desk looks at what is open (sync): client change requests
not yet re-issued or declined, client approvals, open quotation changes, approval and engine requests waiting for a
decision, and open tasks (decisions for the person who asked, stop alerts, "quotation needs update"). Each open thing
gives one notification per person it concerns; when the thing is resolved, the notification moves to Resolved for
everyone. The first sync starts from what is open then, so earlier history does not flood the bell.

Who gets what: client changes / approvals -> the order's salesperson, the designers who worked on the project, the
administrators; approval and engine requests -> the administrators (the secondary administrator for what he may decide);
quotation changes -> salesperson + designers; tasks (decisions, stop alerts ...) -> the person the task is for.
Email only for stop alerts (sent when they are made; see alerts.py)."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db, auth
from .app import T, User, ctx

router = APIRouter()
ICON = dict(client="✎", approved="✓", quote="⇄", change="?", engine="⚙", task="•", stop="!")


def _users(s):
    return [u for u in s.scalars(select(db.User).where(db.User.active == True))]         # noqa: E712


def _team(s, order, users):
    """The order's salesperson (when a desk user), the designers who worked on it, the administrators."""
    names = {u.name for u in users if u.role == "admin"}
    if order.salesperson and any(u.name == order.salesperson for u in users):
        names.add(order.salesperson)
    worked = set()
    for l in order.lines:
        worked |= {r.issued_by for r in l.revisions} | ({l.drawing.made_by} if l.drawing else set()) | {q.answered_by for q in l.questions if q.answered_by}
    names |= {u.name for u in users if u.name in worked and u.role in ("drawing", "admin2", "coordinator")}
    return names


def _open_items(s):
    """-> {source: dict(kind, title, ref, url, people, info)} for everything open now."""
    users = _users(s)
    admins = [u for u in users if auth.level(u) in ("admin", "admin2")]
    out = {}
    start = s.scalar(select(db.Notification).where(db.Notification.source == "_start"))
    for o in s.scalars(select(db.Order)):
        team = None
        for l in o.lines:
            if not l.revisions or l.cancelled_at:
                continue
            r = l.revisions[-1]
            ap = s.scalars(select(db.Approval).where(db.Approval.revision_id == r.id).order_by(db.Approval.id.desc())).first()
            if ap is None:
                continue
            team = team if team is not None else _team(s, o, users)
            short = "%s · Product %d" % (o.client.name.split()[0] if o.client else o.source_ref, l.no)
            if ap.outcome == "changes":
                done = s.scalar(select(db.ClientAction.id).where(db.ClientAction.approval_id == ap.id, db.ClientAction.kind.in_(("reissued", "reject"))).limit(1))
                if not done:
                    out["client:%d" % ap.id] = dict(kind="client", title="%s · client asked for changes" % short, ref=o.source_ref,
                                                    url="/orders/%s/work#client" % o.source_ref, people=team, info=False)
            elif ap.outcome == "approved" and start is not None and ap.at and ap.at.replace(tzinfo=None) > start.at.replace(tzinfo=None):
                out["approved:%d" % ap.id] = dict(kind="approved", title="%s · client approved Rev %s" % (short, r.rev), ref=o.source_ref,
                                                  url="/orders/%s/view/%d?rev=%d" % (o.source_ref, l.no, r.id), people=team, info=True)
        qcs = list(s.scalars(select(db.QuoteChange).where(db.QuoteChange.order_id == o.id, db.QuoteChange.status == "open")))
        if qcs:
            team = team if team is not None else _team(s, o, users)
            people = team                                                # salesperson + designers on it (+ administrators)
            for qc in qcs:
                what = {"added": "added", "dropped": "dropped", "qty": "quantity changed", "spec": "specification changed"}.get(qc.kind, qc.kind)
                out["quote:%d" % qc.id] = dict(kind="quote", title="%s · quotation: %s%s" % (o.source_ref, ("Product %d " % qc.line_no) if qc.line_no else "", what),
                                               ref=o.source_ref, url="/orders/%s/changes" % o.source_ref, people=people, info=False)
    for pr in s.scalars(select(db.PortalRequest).where(db.PortalRequest.status.in_(("received", "handling")))):   # issue #47
        o = s.get(db.Order, pr.order_id)
        if o is None:
            continue
        out["portalreq:%d" % pr.id] = dict(kind="client", title="%s · client asks to %s a product" % (o.source_ref, pr.kind), ref=o.source_ref,
                                           url="/orders/%s/work#requests" % o.source_ref, people=_team(s, o, users), info=False)
    for cr in s.scalars(select(db.ChangeRequest).where(db.ChangeRequest.status == "asked")):
        tab = {"after_approval": "after", "revision": "revision", "production": "production"}.get(cr.kind, "after")
        out["change:%d" % cr.id] = dict(kind="change", title="%s · %s waiting for your decision" % (cr.order_ref, {"after_approval": "change after approval", "revision": "revision raise",
                                        "production": "production change"}.get(cr.kind, "request")), ref=cr.order_ref, url="/approvals?tab=%s&show=%d" % (tab, cr.id),
                                        people={u.name for u in admins if auth.can_decide(u, cr.kind)}, info=False)
    for er in s.scalars(select(db.EngineRequest).where(db.EngineRequest.status == "asked")):
        out["engine:%d" % er.id] = dict(kind="engine", title="%s · engine request: %s" % (er.order_ref or "Engines", er.text[:60]), ref=er.order_ref,
                                        url="/approvals?tab=engine&show=%d" % er.id, people={u.name for u in admins if auth.can_decide(u, "engine")}, info=False)
    roles = {u.name: u.role for u in users}
    for t in s.scalars(select(db.Task).where(db.Task.status == "open")):
        stop = t.text.startswith("STOP")
        if roles.get(t.assignee) == "production":
            url = "/production"
        elif (t.source or "").startswith(("engine:", "change:")):
            url = "/my-requests"
        elif (t.source or "").startswith("quotestop:"):
            url = "/orders/%s/changes" % t.order_ref
        else:
            url = "/orders/%s" % t.order_ref if t.order_ref else "/"
        out["task:%d" % t.id] = dict(kind="stop" if stop else "task", title="%s · %s" % (t.order_ref or "Desk", t.text[:90]), ref=t.order_ref, url=url,
                                     people={t.assignee}, info=False)
    return out


def sync(s):
    """Brings the notifications in line with what is open now. Idempotent; cheap enough to run on each page."""
    if s.scalar(select(db.Notification.id).where(db.Notification.source == "_start")) is None:
        s.add(db.Notification(user_name="", source="_start", kind="", resolved_at=db.now()))   # earlier history is not notified
        s.flush()
    items = _open_items(s)
    have = {}
    for n in s.scalars(select(db.Notification).where(db.Notification.source != "_start")):
        have[(n.user_name, n.source)] = n
    for src, it in items.items():
        for name in it["people"]:
            n = have.get((name, src))
            if n is None:
                s.add(db.Notification(user_name=name, source=src, kind=it["kind"], title=it["title"], order_ref=it["ref"] or "", url=it["url"]))
            elif n.resolved_at is not None and not it["info"]:
                n.resolved_at = None                                     # open again (e.g. a new request on the same thing)
    for (name, src), n in have.items():
        if n.resolved_at is None and src not in items:
            n.resolved_at = db.now()                                     # the thing was resolved: for everyone
    s.commit()


def counts(user):
    with db.Session() as s:
        try:
            sync(s)
        except Exception:
            s.rollback()
        rows = list(s.scalars(select(db.Notification).where(db.Notification.user_name == user.name, db.Notification.resolved_at.is_(None))
                              .order_by(db.Notification.id.desc())))
    return len(rows), rows[:12]


@router.get("/notifications", response_class=HTMLResponse)
def page(request: Request, show: str = "open", ref: str = "", user=User):
    show = show if show in ("open", "resolved") else "open"
    with db.Session() as s:
        sync(s)
        q = select(db.Notification).where(db.Notification.user_name == user.name)
        q = q.where(db.Notification.resolved_at.is_(None) if show == "open" else db.Notification.resolved_at.is_not(None))
        if ref:
            q = q.where(db.Notification.order_ref == ref)
        rows = list(s.scalars(q.order_by(db.Notification.id.desc()).limit(200)))
        refs = sorted({n.order_ref for n in s.scalars(select(db.Notification).where(db.Notification.user_name == user.name)) if n.order_ref})
    return T.TemplateResponse(request, "notifications.html", ctx(user, rows=rows, show=show, ref=ref, refs=refs, icon=ICON, nav="notes"))


@router.get("/notifications/{nid}/open")
def open_one(nid: int, user=User):
    """Opens the thing a notification points at. It is marked seen; it stays open until that thing is resolved
    (a note that needs no action is resolved now)."""
    with db.Session() as s:
        n = s.get(db.Notification, nid)
        if n is None or n.user_name != user.name:
            raise HTTPException(404)
        n.seen_at = n.seen_at or db.now()
        if n.kind == "approved":
            n.resolved_at = n.resolved_at or db.now()
        url = n.url or "/"
        s.commit()
    return RedirectResponse(url, status_code=303)
