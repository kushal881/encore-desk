"""Client approvals and change requests (issue #34): one card per issued drawing on the Questions and changes page, apart
from the main conversation, with the client's answer (Waiting / Approved / Changes asked), who and when, the client's
numbered marks and notes, and a link to the marked-up drawing.

On a "Changes asked" card:
- Apply change: the client's notes (editable) go to the AI change panel of that product as a pending change; the AI only
  changes the product file, Accept / Undo and the checks still apply.
- Re-issue to client: once the change is accepted and the preview is ready, one step issues the next revision, makes the
  combined PDF and emails "Revised drawings" with the portal link to the portal access list (what will be sent is shown
  first). Refused while an AI change waits, a check fails, or the drawing is locked, as with any issue.
- Reply / Reject: an answer to the client without changing the drawing, shown to the client on the portal.
Changes asked by email or phone (recorded by staff on the Issue screen) come to the same card.
Routes are kept here, apart from app.py; app.py includes this router."""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db, issuing
from .app import T, User, ctx, load, _current, _approval_of, _ready, _why_not_ready, _locked

router = APIRouter()
STATUS = dict(waiting="Waiting", approved="Approved", changes="Changes asked")


def cards(o):
    """-> [dict(l, r, ap, status, marks, actions, ...)] for the order's issued drawings, in serial order."""
    out = []
    with db.Session() as s:
        for l, r in _current(o):
            ap = _approval_of(r)
            status = "waiting" if ap is None else ("approved" if ap.outcome == "approved" else "changes")
            marks = [m for m in s.scalars(select(db.Mark).where(db.Mark.line_id == l.id).order_by(db.Mark.no))
                     if (m.shown == "r%d" % r.id or m.shown.startswith("r%ds" % r.id)) and (m.by or "").startswith("Client:")]
            acts = list(s.scalars(select(db.ClientAction).where(db.ClientAction.approval_id == ap.id).order_by(db.ClientAction.id))) if ap else []
            prev = None                                                        # the answer on the revision before, when re-issued since
            if ap is None and len(l.revisions) > 1:
                prev = _approval_of(l.revisions[-2])
            done = any(a.kind in ("reissued", "reject") for a in acts)
            out.append(dict(l=l, r=r, ap=ap, status=status, status_text=STATUS[status], marks=marks, actions=acts, prev=prev,
                            open=status == "changes" and not done, applied=any(a.kind == "applied" for a in acts),
                            ai_waiting=getattr(l, "ai_waiting", False), can_reissue=_ready(l) and not l.issued_current and not _locked(o, l),
                            why_not=_why_not_ready(l) if not _ready(l) else ("" if not l.issued_current else "the change has not been made yet")))
    return out


def open_count(o):
    """Drawings whose client asked for changes that are not dealt with yet (re-issued or answered with a rejection)."""
    return sum(1 for c in cards(o) if c["open"])


def _card(o, aid):
    c = next((c for c in cards(o) if c["ap"] is not None and c["ap"].id == aid), None)
    if c is None:
        raise HTTPException(404, "This answer is not on a current drawing of %s" % o.source_ref)
    return c


def _log(o, aid, kind, text, user, message):
    with db.Session() as s:
        s.add(db.ClientAction(order_id=o.id, approval_id=aid, kind=kind, text=text[:2000], by=user.name))
        s.add(db.Message(order_id=o.id, who="desk", text=message))
        s.commit()


@router.post("/orders/{ref}/client/{aid}/apply")
def apply_change(ref: str, aid: int, text: str = Form(""), user=User):
    """Sends the client's notes (as edited) to the product's AI change panel as a pending change."""
    o = load(ref)
    c = _card(o, aid)
    text = " ".join(text.split())
    if c["status"] != "changes":
        raise HTTPException(400, "The client has not asked for changes on this drawing")
    if not text:
        raise HTTPException(400, "Write the change first (the client's notes can be edited, not left empty)")
    if c["ai_waiting"]:
        raise HTTPException(400, "Product %d: a change is already waiting in the change panel. Accept or Undo it first." % c["l"].no)
    from .app import ai_ask
    resp = ai_ask(ref, c["l"].no, text=text, marker=0, user=user)          # the normal change panel: trial, checks, Accept / Undo
    _log(o, aid, "applied", text, user, "Client's change on Product %d (%s Rev %s) sent to the change panel by %s: %s" % (c["l"].no, c["r"].number, c["r"].rev, user.name, text))
    return resp


def _plan(request, o, c, user):
    """What re-issuing will send: the next revision, the recipients (portal access list, approvers) and the email."""
    with db.Session() as s:
        access = list(s.scalars(select(db.PortalAccess).where(db.PortalAccess.order_id == o.id).order_by(db.PortalAccess.id)))
    to = [a.email for a in access if a.can_approve] or [a.email for a in access]
    nxt = "%s Rev %s" % (c["r"].number, chr(ord(c["l"].last_rev.rev) + 1))
    others = ["%s Rev %s" % (r.number, r.rev) for l, r in _current(o) if l.no != c["l"].no]
    contact = (o.client.contact or "").split(",")[0].strip()
    subject, body = issuing.fill("revised", o.source_ref, o.client.name, contact, [nxt] + others, user.name)
    return dict(to=to, next=nxt, subject=subject, body=body, mail_on=issuing.configured())


@router.get("/orders/{ref}/client/{aid}/reissue", response_class=HTMLResponse)
def reissue_page(request: Request, ref: str, aid: int, user=User):
    o = load(ref)
    c = _card(o, aid)
    return T.TemplateResponse(request, "client_reissue.html", ctx(user, o=o, c=c, plan=_plan(request, o, c, user), tab="work"))


@router.post("/orders/{ref}/client/{aid}/reissue")
def reissue(request: Request, ref: str, aid: int, user=User):
    """One step: issue the next revision of this product, make the combined PDF and email the revised set."""
    o = load(ref)
    c = _card(o, aid)
    if c["status"] != "changes" or not c["open"]:
        raise HTTPException(400, "There is no open change request on this drawing")
    if c["ai_waiting"]:
        raise HTTPException(400, "Product %d: Accept or Undo the change first." % c["l"].no)
    if not c["can_reissue"]:
        raise HTTPException(400, "Product %d cannot be re-issued yet: %s" % (c["l"].no, c["why_not"] or "it is locked"))
    plan = _plan(request, o, c, user)
    if not plan["to"]:
        raise HTTPException(400, "Nobody is on the portal access list of this order. Add the client's email on the Issue screen first.")
    from .app import issue, email_send
    issue(ref, folder_no="", nos=str(c["l"].no), back="issue", user=user)    # the normal issue: numbers, revision, checks
    o2 = load(ref)
    l2 = next(x for x in o2.lines if x.no == c["l"].no)
    revs = ["%s Rev %s" % (r.number, r.rev) for _, r in _current(o2)]
    contact = (o2.client.contact or "").split(",")[0].strip()
    subject, body = issuing.fill("revised", o2.source_ref, o2.client.name, contact, revs, user.name)
    email_send(request, ref, kind="revised", to=", ".join(plan["to"]), cc="", subject=subject, body=body, attach="1", user=user)
    with db.Session() as s:
        log = s.scalars(select(db.EmailLog).where(db.EmailLog.order_id == o.id).order_by(db.EmailLog.id.desc())).first()
    sent = log is not None and not log.error
    _log(o, aid, "reissued", "%s Rev %s %s" % (l2.last_rev.number, l2.last_rev.rev, ("sent to " + ", ".join(plan["to"])) if sent else "issued; the email was not sent"),
         user, "Product %d re-issued to the client by %s as %s Rev %s after their change request%s." % (
             l2.no, user.name, l2.last_rev.number, l2.last_rev.rev, "" if sent else " (the email could not be sent: %s)" % (log.error if log else "not set up")))
    return RedirectResponse("/orders/%s/work#client" % ref, status_code=303)


@router.post("/orders/{ref}/client/{aid}/reply")
def reply(ref: str, aid: int, text: str = Form(""), kind: str = Form("reply"), user=User):
    """An answer to the client without changing the drawing. Shown to the client on the portal. A rejection closes the
    request: the drawing stays as it is."""
    o = load(ref)
    c = _card(o, aid)
    text = text.strip()
    if kind not in ("reply", "reject"):
        raise HTTPException(400)
    if not text:
        raise HTTPException(400, "Write the answer to the client first")
    if c["status"] != "changes":
        raise HTTPException(400, "The client has not asked for changes on this drawing")
    _log(o, aid, kind, text, user, "%s to the client on Product %d (%s Rev %s) by %s: %s" % (
        "Reply" if kind == "reply" else "Change request declined", c["l"].no, c["r"].number, c["r"].rev, user.name, text))
    return RedirectResponse("/orders/%s/work#client" % ref, status_code=303)


def replies_for(approval_id):
    """Staff answers to show the client on the portal: [ClientAction] of kind reply / reject / reissued."""
    if not approval_id:
        return []
    with db.Session() as s:
        return list(s.scalars(select(db.ClientAction).where(db.ClientAction.approval_id == approval_id,
                                                             db.ClientAction.kind.in_(("reply", "reject", "reissued"))).order_by(db.ClientAction.id)))
