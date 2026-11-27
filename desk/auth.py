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
ROLES = {"admin": "Administrator", "drawing": "Drawing user", "coordinator": "Sales coordinator"}


class NotSignedIn(Exception):
    pass


def seed(s):
    if s.scalar(select(db.User.id).limit(1)) is None:
        for row in os.environ.get("DESK_USERS", DEFAULT_USERS).split(";"):
            email, name, role = (x.strip() for x in row.split("|"))
            s.add(db.User(email=email.lower(), name=name, role=role))
        s.commit()


def current_user(request: Request):
    """The signed-in user, from our own list, checked on every request (so removing someone takes effect at once)."""
    uid = request.session.get("uid")
    if uid:
        with db.Session() as s:
            u = s.get(db.User, uid)
            if u and u.active:
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
    return RedirectResponse("/", status_code=303)


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
        if u is None:
            return RedirectResponse("/login?error=notlisted")
        request.session.clear()
        request.session["uid"] = u.id
    return RedirectResponse("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
