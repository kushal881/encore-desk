"""First-time guided tour (issue #43). Desk: on the first sign-in of each person, a short tour of the left menu, the
dashboard counts, New project, a project's next step and the Guide; administrators get two more steps (Approvals and the
Engines box); production users a tour of the Production page. Portal: on the first sign-in of each client email, a tour
of their orders. Remembered on the server (not only the browser); "Take the tour" on the Guide page and "Take the tour
again" in the portal footer replay it. The tour never changes data and never blocks work: Skip and Esc always close it."""
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from markupsafe import Markup
from . import db, portal
from .app import User

router = APIRouter()

STAFF = [
    dict(sel=".sidenav", title="The menu", text="The same on every page: Dashboard, New project, Projects, My requests and the Guide."),
    dict(sel=".tiles", title="Your counts", text="Every count can be clicked: active projects, drawings pending, waiting for the client, in production."),
    dict(sel=".sidenav a[href='/new']", title="Start a project", text="New project: pick an open Odoo quotation and the server folder number, or remake a past drawing."),
    dict(sel="table a.btn, #mysteps a.btn", title="One clear next step", text="Each project has a stage bar and one big next-step button. If unsure, press the teal button."),
    dict(sel=".sidenav a[href='/guide']", title="The Guide", text="Step-by-step help for every screen. The ? next to a page title opens the right section."),
]
ADMIN_EXTRA = [
    dict(sel=".sidenav a[href='/approvals']", title="Approvals", text="Everything staff cannot do alone waits here: engine changes, changes after client approval, revision raises."),
    dict(sel="form[action='/engines/refresh'], .card:has(form[action='/engines/refresh'])", title="Engines", text="The approved engine version in use. When a newer one is approved, update it here; issued drawings stay on theirs."),
]
PRODUCTION = [
    dict(sel=".sidenav", title="Production", text="Your page: every product in production, with its production drawing (P1, P2 ...) and whether it is released."),
    dict(sel="table a.btn", title="Open a production sheet", text="The production drawing, bill of material and cut list, and a copy for your department."),
    dict(sel=".sidenav a[href='/guide']", title="The Guide", text="Step-by-step help. Stop alerts appear at the top of the Production page until you mark them done."),
]
PORTAL = [
    dict(sel=".tiles", title="Your orders", text="Each quotation and how many drawings wait for your approval."),
    dict(sel="a.btn[href^='/portal/orders/']", title="Review drawings", text="Opens the drawing set of that order."),
    dict(sel=".bar", title="Drawing status", text="Each drawing is Waiting, Approved or Changes asked."),
    dict(sel=None, title="Approve a drawing", text="On a drawing, zoom in, then Approve this drawing: your name and a one-time code we email you."),
    dict(sel=None, title="Or ask for a change", text="Tap Mark a spot to change, write what to change for each number, and Send. We revise the drawing for your approval."),
    dict(sel="a[href='/portal/past']", title="Past orders", text="Download your final sets, and ask to reorder."),
]


def desk_steps(user):
    if user.role == "production":
        return PRODUCTION
    return STAFF + (ADMIN_EXTRA if user.role in ("admin", "admin2") else [])


def script(steps, done_url):
    """The tags that start the tour on this page."""
    return Markup('<script src="/static/tour.js"></script><script>window.addEventListener("load",function(){startTour(%s,%s)});</script>'
                  % (json.dumps(steps), json.dumps(done_url)))


def for_desk(user, replay=False):
    """The tour tags for a desk page, or '' when this person has seen it (unless replayed)."""
    return script(desk_steps(user), "/tour/done") if (replay or not getattr(user, "tour_done", False)) else ""


def for_portal(email, replay=False):
    with db.Session() as s:
        seen = s.get(db.PortalTour, email) is not None
    return script(PORTAL, "/portal/tour/done") if (replay or not seen) else ""


@router.post("/tour/done")
def desk_done(user=User):
    with db.Session() as s:
        u = s.get(db.User, user.id)
        u.tour_done = True; s.commit()
    return JSONResponse({"ok": True})


@router.post("/portal/tour/done")
def portal_done(request: Request):
    email = portal.current(request)
    with db.Session() as s:
        if s.get(db.PortalTour, email) is None:
            s.add(db.PortalTour(email=email)); s.commit()
    return JSONResponse({"ok": True})
