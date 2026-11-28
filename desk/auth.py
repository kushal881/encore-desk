"""Sign-in. Staff sign in with their Encore Google account; only people in the desk's own user list get in.
The desk never sees or stores a staff password. Client logins (step 3 of the build) are separate and not here yet."""
import os, secrets, urllib.parse
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . import db

router = APIRouter()
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
DOMAIN = os.environ.get("DESK_DOMAIN", "encoreprimefurniture.com")
ON_SERVER = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
DEV = os.environ.get("DESK_DEV_LOGIN") == "1" and not ON_SERVER           # pick-a-user sign-in for building; never on the server
DEFAULT_USERS = "kushal@encoreprimefurniture.com|Kushal Singhal|admin;sushil@encoreprimefurniture.com|Sushil Singhal|drawing;info@encoreprimefurniture.com|Anchal Dawar|coordinator"
ROLES = {"admin": "Administrator", "admin2": "Secondary administrator", "drawing": "Designer", "coordinator": "Sales coordinator",
         "sales": "Salesperson", "production": "Production"}
# roles by email (Kushal 28-Nov-2026): design@encore... -> Designer, production@encore... -> Production, sales@encore... -> Salesperson.
# An account not on the user list whose address starts with one of these words is added with that role at its first sign-in;
# people already on the list keep the role the administrator gave them.
PREFIX_ROLES = (("design", "drawing"), ("production", "production"), ("sales", "sales"))

# What each role sees (Kushal 28-Nov-2026): the administrator everything; designers and the sales coordinator everything but
# the administrator's controls, production included; a salesperson everything but the administrator's controls and the
# production drawings; production only what is about production.
PRODUCTION_PATHS = (r"^/orders/[^/]+/production(/|$)", r"^/production$", r"^/sets/", r"^/production-files/", r"^/prodpreview/", r"^/prodchange/",
                    r"^/orders/[^/]+/sets/", r"^/orders/[^/]+/bom$", r"^/orders/[^/]+/view/\d+/production$")
PRODUCTION_ALSO = (r"^/$", r"^/photos/", r"^/tasks/\d+/done$", r"^/static/", r"^/guide", r"^/notifications")       # what production users also reach


def role_for_email(email):
    """The role an unlisted @DOMAIN account gets from the start of its address, or None."""
    local = (email or "").lower().split("@")[0]
    return next((role for word, role in PREFIX_ROLES if local.startswith(word)), None)


def sees_production(user):
    return user.role != "sales"


def sees_client(user):
    return user.role != "production"


def allowed(user, path, query=""):
    """Whether this user may open this address (checked on every request, so a hidden link cannot be typed in)."""
    import re as _re
    prod = any(_re.search(p, path) for p in PRODUCTION_PATHS) or (_re.search(r"^/orders/[^/]+/view/\d+$", path) and "mode=prod" in (query or ""))
    if user.role == "production":
        return prod or any(_re.search(p, path) for p in PRODUCTION_ALSO)
    if user.role == "sales":
        return not prod
    return True


class NotAllowed(Exception):
    pass


# secondary administrators (decision 8): approve requests and day-to-day changes, but no engine changes, no role changes and
# no unlocking of client-approved drawings. Set in Railway as DESK_SECONDARY_ADMINS (emails, comma separated).
SECONDARY = {e.strip().lower() for e in os.environ.get("DESK_SECONDARY_ADMINS", "sushil@encoreprimefurniture.com").split(",") if e.strip()}


def level(user):
    """admin | admin2 (secondary administrator) | staff"""
    if user.role == "admin":
        return "admin"
    return "admin2" if user.role == "admin2" or (user.email or "").lower() in SECONDARY else "staff"


def can_decide(user, kind):
    """Who decides which request: engine changes and changes after client approval only the administrator;
    revision raises and production (construction) changes after release also a secondary administrator."""
    lv = level(user)
    return lv == "admin" or (lv == "admin2" and kind in ("revision", "production"))


class NotSignedIn(Exception):
    pass


def seed(s):
    if s.scalar(select(db.User.id).limit(1)) is None:
        for row in os.environ.get("DESK_USERS", DEFAULT_USERS).split(";"):
            email, name, role = (x.strip() for x in row.split("|"))
            s.add(db.User(email=email.lower(), name=name, role=role))
        s.commit()
    # issue #40: the secondary administrators listed in DESK_SECONDARY_ADMINS become the Secondary administrator role
    # (once; afterwards the Users screen is where roles change)
    for u in s.scalars(select(db.User).where(db.User.role.in_(("drawing", "coordinator")))):
        if (u.email or "").lower() in SECONDARY:
            u.role = "admin2"
            s.add(db.UserLog(user_id=u.id, text="Role set to Secondary administrator (was listed as secondary administrator)", by="desk"))
    s.commit()


def current_user(request: Request):
    """The signed-in user, from our own list, checked on every request (so removing someone takes effect at once)."""
    uid = request.session.get("uid")
    if uid:
        with db.Session() as s:
            u = s.get(db.User, uid)
            if u and u.active:
                if not allowed(u, request.url.path, request.url.query):
                    raise NotAllowed(u.role)
                return u
    raise NotSignedIn()


def _page(body):
    return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Encore drawing desk</title>'
                        '<style>body{margin:0;font-family:Inter,system-ui,sans-serif;background:#F4F3EF;color:#17191C;display:flex;min-height:100vh;align-items:center;justify-content:center}'
                        '.c{background:#fff;border:1px solid #E3E1DA;border-radius:16px;padding:32px;width:340px;display:flex;flex-direction:column;gap:14px}'
                        'a.b,button{display:flex;align-items:center;justify-content:center;min-height:44px;border-radius:10px;background:#0E6B5C;font-weight:600;color:#fff;text-decoration:none;border:none;font:inherit;cursor:pointer;width:100%}'
                        '.s{font-size:13px;color:#5B5F66;line-height:1.5}.e{font-size:13px;color:#B91C1C}</style></head><body><div class="c">'
                        '<div style="display:flex;align-items:center;gap:12px"><img src="/static/encore-logo.png" alt="Encore Prime logo" style="width:48px;height:48px;border-radius:4px"><div><div style="font-size:18px;font-weight:700">Encore Prime</div><div class="s">Drawing desk</div></div></div>' + body + "</div></body></html>")


@router.get("/login")
def login(request: Request, error: str = ""):
    msg = {"notlisted": "This Google account is not on the desk's user list. Ask Kushal to add you.",
           "failed": "Google sign-in did not complete. Try again.", "setup": "Google sign-in is not set up on this server yet."}.get(error, "")
    body = ('<div class="e">%s</div>' % msg if msg else "")
    if DEV:
        with db.Session() as s:
            users = list(s.scalars(select(db.User).where(db.User.active == True)))        # noqa: E712
        body += '<div class="s">Building mode: pick a user. This screen does not exist on the server.</div>' + "".join(
            '<form method="post" action="/login/dev/%d"><button>%s (%s)</button></form>' % (u.id, u.name, ROLES.get(u.role, u.role)) for u in users)
    else:
        body += '<a class="b" href="/auth/google">Sign in with Google</a><div class="s">Use your @%s account. Only people on the user list can sign in.</div>' % DOMAIN
    return _page(body)


@router.post("/login/dev/{uid}")
def dev_login(request: Request, uid: int):
    if not DEV:
        raise HTTPException(404)
    request.session["uid"] = uid
    _signed_in(uid)
    return RedirectResponse("/", status_code=303)


def _signed_in(uid):
    with db.Session() as s:
        u = s.get(db.User, uid)
        if u is not None:
            u.last_sign_in = db.now(); s.commit()


def _redirect_uri(request):
    return os.environ.get("DESK_URL", str(request.base_url)).rstrip("/") + "/auth/callback"


@router.get("/auth/google")
def google(request: Request):
    if not CLIENT_ID:
        return RedirectResponse("/login?error=setup")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    q = dict(client_id=CLIENT_ID, redirect_uri=_redirect_uri(request), response_type="code", scope="openid email profile", state=state, hd=DOMAIN, prompt="select_account")
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q))


def google_identity(code, redirect_uri):
    """Exchanges the code with Google and returns what Google says about the account."""
    t = httpx.post("https://oauth2.googleapis.com/token", data=dict(code=code, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=redirect_uri,
                                                                     grant_type="authorization_code"), timeout=15)
    t.raise_for_status()
    u = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": "Bearer " + t.json()["access_token"]}, timeout=15)
    u.raise_for_status()
    return u.json()


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not code or not state or state != request.session.pop("oauth_state", None):
        return RedirectResponse("/login?error=failed")
    try:
        who = google_identity(code, _redirect_uri(request))
    except Exception:
        return RedirectResponse("/login?error=failed")
    email = (who.get("email") or "").lower()
    if not who.get("email_verified") or not email.endswith("@" + DOMAIN):
        return RedirectResponse("/login?error=notlisted")
    with db.Session() as s:
        u = s.scalar(select(db.User).where(db.User.email == email, db.User.active == True))       # noqa: E712
        if u is None and role_for_email(email) and s.scalar(select(db.User).where(db.User.email == email)) is None:
            u = db.User(email=email, name=(who.get("name") or email.split("@")[0]).strip()[:120], role=role_for_email(email))
            s.add(u); s.commit()                                         # a role mailbox (design@, production@, sales@)
        if u is None:
            return RedirectResponse("/login?error=notlisted")
        request.session.clear()
        request.session["uid"] = u.id
        u.last_sign_in = db.now(); s.commit()
    return RedirectResponse("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
