"""Users & roles (issue #40), administrator only. List of staff with role, active and last sign-in; add a person (an
@encoreprimefurniture.com address), change a role, switch someone off or on. Never deleted: history keeps names. Every
change is logged. Roles (decision 8, Kushal 28-Nov-2026): Administrator, Secondary administrator (approves requests and
day-to-day changes; no engine changes, no role changes, no unlocking client-approved drawings), Designer, Sales
coordinator, Salesperson (no production drawings), Production (production only)."""
import os
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db, auth, issuing
from .app import T, User, ctx

router = APIRouter()
WHAT = {"admin": "Everything, including engine changes, roles and unlocking client-approved drawings",
        "admin2": "Approves requests and day-to-day changes; no engine changes, no role changes, no unlocking approved drawings",
        "drawing": "Specify, generate, review, change requests; production included",
        "coordinator": "Everything but the administrator's controls; production included",
        "sales": "Projects, quotation changes, client emails, portal access; no production drawings",
        "production": "Production drawings, BOM and cut lists only; receives stop alerts"}


def _admin(user):
    if user.role != "admin":
        raise HTTPException(403, "Only the administrator manages users")


def _back(error="", notice=""):
    from urllib.parse import quote
    q = "&".join(k + "=" + quote(v) for k, v in (("error", error), ("notice", notice)) if v)
    return RedirectResponse("/users" + ("?" + q if q else ""), status_code=303)


INVITE_SUBJECT = "Your access to the Encore drawing desk"


def invite_text(u, base):
    """The invitation email (issue #67, Kushal's draft)."""
    guides = "the Staff Guide and the Directors Guide (menu, Guide)" if u.role in ("admin", "admin2") else "the Staff Guide (menu, Guide)"
    return ("Dear %s,\n\n"
            "You have been added to the Encore drawing desk as %s: %s.\n\n"
            "Open %s and click Sign in with Google, using your %s account. No separate password is needed.\n\n"
            "Start with %s.\n\n"
            "If you can't sign in, reply to this email.\n\n"
            "Encore Prime Furniture" % (u.name, auth.ROLES.get(u.role, u.role), (lambda w: w[:1].lower() + w[1:])(WHAT.get(u.role, "").rstrip(".")), base, u.email, guides))


def _invite(s, u, base, by):
    """Sends the invitation and logs it. -> "" when sent, otherwise the reason it could not be sent."""
    try:
        issuing.send([u.email], [], INVITE_SUBJECT, invite_text(u, base))
    except Exception as e:                                                 # noqa: BLE001 -- adding the person never fails over the email
        return str(e) or type(e).__name__
    u.invited_at = db.now()
    s.add(db.UserLog(user_id=u.id, text="Invitation sent to %s (%s)" % (u.name, u.email), by=by))
    return ""


def _base(request):
    return os.environ.get("DESK_URL", str(request.base_url)).rstrip("/")


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, error: str = "", notice: str = "", user=User):
    _admin(user)
    with db.Session() as s:
        people = list(s.scalars(select(db.User).order_by(db.User.active.desc(), db.User.name)))
        log = list(s.scalars(select(db.UserLog).order_by(db.UserLog.id.desc()).limit(40)))
        names = {u.id: u.name for u in people}
    return T.TemplateResponse(request, "users.html", ctx(user, people=people, roles=auth.ROLES, what=WHAT, log=log, names=names, error=error, notice=notice,
                                                          domain=auth.DOMAIN, me=user.id, nav="users"))


@router.post("/users/add")
def add(request: Request, email: str = Form(""), name: str = Form(""), role: str = Form("drawing"), invite: str = Form(""), user=User):
    _admin(user)
    email, name = email.strip().lower(), " ".join(name.split())[:120]
    if not name:
        return _back("Enter the person's name")
    if not email.endswith("@" + auth.DOMAIN) or email.count("@") != 1 or len(email) <= len(auth.DOMAIN) + 1:
        return _back("The email must be an @%s address" % auth.DOMAIN)
    if role not in auth.ROLES:
        return _back("Pick a role")
    with db.Session() as s:
        if s.scalar(select(db.User).where(db.User.email == email)):
            return _back("%s is already on the list. Change their role or switch them on below." % email)
        u = db.User(email=email, name=name, role=role)
        s.add(u); s.flush()
        s.add(db.UserLog(user_id=u.id, text="Added %s (%s) as %s" % (name, email, auth.ROLES[role]), by=user.name))
        s.commit()
        if not invite:
            return _back(notice="Added %s. No invitation was sent." % name)
        why = _invite(s, u, _base(request), user.name)
        s.commit()
    if why:
        return _back("Added, but the invitation could not be sent: %s" % why)
    return _back(notice="Added %s and sent the invitation to %s." % (name, email))


@router.post("/users/{uid}/invite")
def invite(request: Request, uid: int, user=User):
    """Send invite / Send again: for active people who have not signed in yet."""
    _admin(user)
    with db.Session() as s:
        u = s.get(db.User, uid)
        if u is None:
            raise HTTPException(404)
        if not u.active:
            return _back("%s is switched off: switch them on before inviting them" % u.name)
        if u.last_sign_in:
            return _back("%s has already signed in" % u.name)
        again = bool(u.invited_at)
        why = _invite(s, u, _base(request), user.name)
        s.commit()
    if why:
        return _back("The invitation could not be sent: %s" % why)
    return _back(notice=("Invitation sent again to %s." if again else "Invitation sent to %s.") % u.email)


@router.post("/users/{uid}/{what}")
def change(uid: int, what: str, role: str = Form(""), user=User):
    """role: change the role; off / on: switch someone off or back on (never deleted)."""
    _admin(user)
    with db.Session() as s:
        u = s.get(db.User, uid)
        if u is None or what not in ("role", "off", "on"):
            raise HTTPException(404)
        if u.id == user.id and (what == "off" or (what == "role" and role != "admin")):
            return _back("You cannot switch yourself off or take away your own administrator role")
        if what == "role":
            if role not in auth.ROLES:
                return _back("Pick a role")
            if role == u.role:
                return _back()
            s.add(db.UserLog(user_id=u.id, text="Role of %s changed from %s to %s" % (u.name, auth.ROLES.get(u.role, u.role), auth.ROLES[role]), by=user.name))
            u.role = role
        else:
            u.active = what == "on"
            s.add(db.UserLog(user_id=u.id, text="%s switched %s" % (u.name, "on" if u.active else "off (cannot sign in)"), by=user.name))
        s.commit()
    return _back()
