"""The client portal (screens 8a PortalLogin, 8b PortalHome, 8c PortalPast, 9 PortalSet, 10 PortalDrawing; decisions 7, 10, 11).

Clients sign in with their email and a one-time 6-digit code sent from info@ (no password). Only emails on an order's
portal access list can sign in, and they see only the orders they are listed on. The link in our drawings email signs
the person it was sent to in directly (each listed recipient gets their own link); approving still needs a fresh code
sent to that same email, so a forwarded email can look but never approve.

Approval (decision 11) is a click-wrap: a person listed as "can approve", a one-time code to that email, the tick
"I approve ... for manufacturing". The record keeps drawing number, revision, the PDF's fingerprint, name, designation,
email, date and time, IP and code-verified, and the client gets a confirmation email with the approved PDF.

The portal shows only issued revisions. Previews, AI trials and anything not yet issued never reach it. Files open
through the storage module's short-lived signed links, and every view and download is logged."""
import datetime, hashlib, hmac, json, os, re, secrets
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select
from . import db, engines, issuing, storage

router = APIRouter(prefix="/portal")
T = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
SECRET = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
COOKIE = "encore_portal"
KEEP_DAYS, SHORT_HOURS, LINK_DAYS = 30, 12, 14
CODE_MINUTES, CODE_TRIES, CODES_PER_HOUR = 10, 5, 5
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
T.env.filters["when"] = lambda d: d.replace(tzinfo=d.tzinfo or datetime.timezone.utc).astimezone(IST).strftime("%d %b %Y") if d else ""
ON_SERVER = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


class PortalSignIn(Exception):
    pass


def _desk():
    from . import app as desk                    # the desk's order helpers (imported late: the desk imports this module)
    return desk


# ---- who is signed in --------------------------------------------------------------------------------------------------

def _ser(salt):
    return URLSafeTimedSerializer(SECRET, salt=salt)


def listed(email):
    """The access-list rows of one email, every order that is not archived. Empty = this email cannot sign in.
    An archived order's rows are kept (Reopen gives the same people access again) but count for nothing (issue #50)."""
    with db.Session() as s:
        return list(s.scalars(select(db.PortalAccess).join(db.Order, db.Order.id == db.PortalAccess.order_id)
                              .where(db.PortalAccess.email == (email or "").strip().lower(), db.Order.archived_at.is_(None))))


def losing_access(order_id):
    """The client emails on this order that are on no other active order: archiving it ends their portal access."""
    with db.Session() as s:
        emails = [a.email for a in s.scalars(select(db.PortalAccess).where(db.PortalAccess.order_id == order_id).order_by(db.PortalAccess.id))]
    return [e for e in dict.fromkeys(emails) if not any(a.order_id != order_id for a in listed(e))]


GONE = "This order is no longer available. Please contact your Encore sales person."


def current(request: Request):
    """The signed-in client's email, checked against the access lists on every request (removing someone works at once)."""
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            data = _ser("portal").loads(raw, max_age=KEEP_DAYS * 86400)
            if data.get("k") or (datetime.datetime.now(datetime.timezone.utc).timestamp() - data.get("t", 0)) < SHORT_HOURS * 3600:
                if listed(data.get("e")):
                    return data["e"]
        except (BadSignature, SignatureExpired):
            pass
    raise PortalSignIn()


def _signed_in(resp, email, keep):
    tok = _ser("portal").dumps(dict(e=email, k=bool(keep), t=datetime.datetime.now(datetime.timezone.utc).timestamp()))
    resp.set_cookie(COOKIE, tok, max_age=KEEP_DAYS * 86400 if keep else None, httponly=True, secure=ON_SERVER, samesite="lax", path="/portal")
    return resp


def link_for(email, order_id, base):
    """The personal sign-in link put in the drawings email for one listed recipient (valid 14 days)."""
    return "%s/portal/link/%s" % (base.rstrip("/"), _ser("portal-link").dumps(dict(e=email.lower(), o=order_id)))


def base_url(request):
    return os.environ.get("DESK_URL", str(request.base_url)).rstrip("/")


# ---- one-time codes ----------------------------------------------------------------------------------------------------

def _hash(email, purpose, order_id, code):
    return hmac.new(SECRET.encode(), ("%s|%s|%d|%s" % (email, purpose, order_id, code)).encode(), hashlib.sha256).hexdigest()


def new_code(email, purpose, order_id=0, ip=""):
    """Makes and emails a 6-digit code. -> "" when sent, otherwise the reason it was not."""
    now = db.now()
    with db.Session() as s:
        recent = s.query(db.PortalCode).filter(db.PortalCode.email == email, db.PortalCode.at > now - datetime.timedelta(hours=1)).count()
        if recent >= CODES_PER_HOUR:
            return "Too many codes were asked for this email in the last hour. Please try again later."
        code = "%06d" % secrets.randbelow(10 ** 6)
        for old in s.scalars(select(db.PortalCode).where(db.PortalCode.email == email, db.PortalCode.purpose == purpose,
                                                          db.PortalCode.order_id == order_id, db.PortalCode.used == False)):   # noqa: E712
            old.used = True                                          # only the newest code works
        s.add(db.PortalCode(email=email, purpose=purpose, order_id=order_id, code_hash=_hash(email, purpose, order_id, code),
                            expires=now + datetime.timedelta(minutes=CODE_MINUTES), ip=ip[:60]))
        s.commit()
    what = "sign in to" if purpose == "signin" else "approve drawings on"
    try:
        issuing.send([email], [], "Your Encore code: %s" % code,
                     "Your one-time code to %s the Encore Prime Furniture client portal is:\n\n    %s\n\nIt is valid for %d minutes. "
                     "If you did not ask for it, you can ignore this email.\n\nEncore Prime Furniture" % (what, code, CODE_MINUTES))
    except RuntimeError:
        return "We could not email the code just now. Please contact your Encore sales person."
    return ""


def check_code(email, purpose, code, order_id=0):
    """-> "" when the code is right (it is then used up), otherwise what is wrong."""
    code = re.sub(r"\D", "", code or "")
    with db.Session() as s:
        pc = s.scalars(select(db.PortalCode).where(db.PortalCode.email == email, db.PortalCode.purpose == purpose, db.PortalCode.order_id == order_id,
                                                   db.PortalCode.used == False).order_by(db.PortalCode.id.desc())).first()   # noqa: E712
        if pc is None:
            return "Ask for a code first."
        exp = pc.expires if pc.expires.tzinfo else pc.expires.replace(tzinfo=datetime.timezone.utc)
        if exp < db.now():
            pc.used = True; s.commit()
            return "This code has expired. Ask for a new one."
        if not hmac.compare_digest(pc.code_hash, _hash(email, purpose, order_id, code)):
            pc.tries += 1
            if pc.tries >= CODE_TRIES:
                pc.used = True
            s.commit()
            return "That code is not right. Ask for a new one." if pc.used else "That code is not right. Check the latest email and try again."
        pc.used = True
        s.commit()
    return ""


def _ip(request):
    return request.client.host if request.client else ""


# ---- what a client may see ---------------------------------------------------------------------------------------------

def _access(email, ref):
    """The order and the access-list row, when this email is listed on it; otherwise 404 (we never say it exists)."""
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        a = s.scalar(select(db.PortalAccess).where(db.PortalAccess.order_id == o.id, db.PortalAccess.email == email)) if o else None
    if a is None:
        raise HTTPException(404, "Not found")
    if o.archived_at:                                                     # archived (issue #50): gone for the client
        raise HTTPException(404, GONE)
    return _desk().load(ref), a


def status_of(ap):
    return "approved" if ap and ap.outcome == "approved" else "changes" if ap else "waiting"


def drawings(o):
    """The order's issued drawings as the client sees them: [dict(l, r, status, ap, sheet, ...)] in serial order."""
    desk = _desk()
    cur = desk._current(o)
    out = []
    for n, (l, r) in enumerate(cur, 1):
        ap = desk._approval_of(r)
        item = (r.spec or {}).get("item", {})
        from .clients import replies_for                                    # issue #34: Encore's answers to the client
        areas = [a.area for a in (getattr(l, "areas", None) or [])] or ([item["area"]] if item.get("area") else [])   # issue #85
        out.append(dict(l=l, r=r, ap=ap, status=status_of(ap), sheet=n, total=len(cur), name=item.get("product_name") or l.name,
                        area=areas[0] if areas else NO_AREA, areas=", ".join(areas) or NO_AREA,
                        replies=replies_for(ap.id if ap is not None and ap.outcome == "changes" else 0),
                        code=item.get("product_code", ""), qty=item.get("qty", int(l.qty or 0)),
                        finishes=[(sw.get("use", ""), sw.get("code", "")) for sw in (r.spec or {}).get("swatches", []) if sw.get("code")]))
    return out


NO_AREA = "No area"


def being_drawn(o, ds):
    """Products of the order not issued yet (issue #85): shown as "Being drawn", never ticked or approved."""
    issued = {d["l"].id for d in ds}
    return [dict(no=l.no, name=l.name.split("(")[0].strip(), qty=int(l.qty or 0), area=([a.area for a in (getattr(l, "areas", None) or [])] or [NO_AREA])[0])
            for l in sorted(o.lines, key=lambda x: x.no) if l.id not in issued and not l.cancelled_at]


def groups(ds, todo):
    """[(area, [row])] for the order page: issued drawings and products being drawn, by area in first-seen order, "No area" last.
    row = dict(kind "d" | "todo", d or t, status)"""
    seen = {}
    for d in ds:
        seen.setdefault(d["area"], []).append(dict(kind="d", d=d, status=d["status"]))
    for t in todo:
        seen.setdefault(t["area"], []).append(dict(kind="todo", t=t, status="drawing"))
    keys = [a for a in seen if a != NO_AREA] + ([NO_AREA] if NO_AREA in seen else [])
    return [(a, seen[a]) for a in keys]


def summary(ds):
    return dict(approved=sum(d["status"] == "approved" for d in ds), changes=sum(d["status"] == "changes" for d in ds),
                waiting=sum(d["status"] == "waiting" for d in ds), total=len(ds))


def _orders(email):
    out = []
    for a in listed(email):
        with db.Session() as s:
            ref = s.get(db.Order, a.order_id).source_ref
        o = _desk().load(ref)
        ds = drawings(o)
        last = max((d["r"].issued_at for d in ds), default=None)
        out.append(dict(o=o, a=a, ds=ds, sum=summary(ds), last=last, past=o.stage == "completed"))
    return out


def page(request, name, email, **kw):
    rows = listed(email) if email else []
    client = ""
    if rows:
        with db.Session() as s:
            o = s.get(db.Order, rows[0].order_id)
            client = s.get(db.Client, o.client_id).name if o else ""
    return T.TemplateResponse(request, "portal/" + name, dict(email=email, client=client, **kw))


# ---- sign in -----------------------------------------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def portal_root(request: Request):
    try:
        current(request)
        return RedirectResponse("/portal/orders", status_code=303)
    except PortalSignIn:
        return RedirectResponse("/portal/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, error: str = ""):
    msg = {"link": "This sign-in link has expired or is no longer valid. Enter your email to get a code.", "gone": GONE}.get(error, "")
    return page(request, "login.html", "", step="email", error=msg, to="")


@router.post("/code", response_class=HTMLResponse)
def send_code(request: Request, email: str = Form("")):
    e = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", e):
        return page(request, "login.html", "", step="email", error="Enter your work email address.", to=email)
    err = new_code(e, "signin", 0, _ip(request)) if listed(e) else ""     # not listed: nothing is sent, and we do not say so
    return page(request, "login.html", "", step="code", error=err, to=e)


@router.post("/signin", response_class=HTMLResponse)
def signin(request: Request, email: str = Form(""), code: str = Form(""), keep: str = Form("")):
    e = (email or "").strip().lower()
    err = check_code(e, "signin", code) if listed(e) else "That code is not right. Check the latest email and try again."
    if err:
        return page(request, "login.html", "", step="code", error=err, to=e)
    return _signed_in(RedirectResponse("/portal/orders", status_code=303), e, bool(keep))


@router.get("/link/{token}")
def link(request: Request, token: str):
    """The link in our drawings email: signs its recipient in directly, while that email is still on the order's list."""
    try:
        data = _ser("portal-link").loads(token, max_age=LINK_DAYS * 86400)
    except (BadSignature, SignatureExpired):
        return RedirectResponse("/portal/login?error=link", status_code=303)
    with db.Session() as s:
        a = s.scalar(select(db.PortalAccess).where(db.PortalAccess.order_id == data.get("o", 0), db.PortalAccess.email == data.get("e", "")))
        o = s.get(db.Order, a.order_id) if a else None
    if a is None or o is None:
        return RedirectResponse("/portal/login?error=link", status_code=303)
    if o.archived_at:
        return RedirectResponse("/portal/login?error=gone", status_code=303)
    return _signed_in(RedirectResponse("/portal/orders/%s" % o.source_ref, status_code=303), a.email, False)


@router.post("/signout")
def signout():
    r = RedirectResponse("/portal/login", status_code=303)
    r.delete_cookie(COOKIE, path="/portal")
    return r


# ---- my orders, past orders ------------------------------------------------------------------------------------------

@router.get("/orders", response_class=HTMLResponse)
def home(request: Request):
    email = current(request)
    rows = _orders(email)
    active, past = [x for x in rows if not x["past"]], [x for x in rows if x["past"]]
    tiles = dict(active=len(active), waiting=sum(x["sum"]["waiting"] for x in active), changes=sum(x["sum"]["changes"] for x in active), past=len(past))
    from .tour import for_portal                                       # issue #43: the client's first-time tour
    return page(request, "home.html", email, active=active, past=past, tiles=tiles, tab="orders",
                tour_tags=for_portal(email, request.query_params.get("tour") == "1"))


@router.get("/past", response_class=HTMLResponse)
def past(request: Request, o: str = "", year: str = "", q: str = "", sent: int = 0):
    email = current(request)
    rows = [x for x in _orders(email) if x["past"]]
    years = sorted({x["last"].year for x in rows if x["last"]}, reverse=True)
    shown = [x for x in rows if (not year or (x["last"] and str(x["last"].year) == year)) and (not q or q.lower() in x["o"].source_ref.lower()
                                                                                             or any(q.lower() in d["name"].lower() for d in x["ds"]))]
    sel = next((x for x in rows if x["o"].source_ref == o), shown[0] if shown else None)
    return page(request, "past.html", email, rows=shown, sel=sel, years=years, year=year, q=q, sent=sent, tab="past")


@router.post("/orders/{ref}/reorder")
async def reorder(request: Request, ref: str):
    """Ticked products of a past order go to the Encore sales person, who prepares a new quotation."""
    email = current(request)
    o, a = _access(email, ref)
    form = await request.form()
    ds = {d["l"].no: d for d in drawings(o)}
    picked = [ds[int(n)] for n in form.getlist("no") if n.isdigit() and int(n) in ds]
    if not picked:
        return RedirectResponse("/portal/past?o=%s&sent=-1" % ref, status_code=303)
    note, site = (form.get("note") or "").strip()[:1000], (form.get("site") or "").strip()[:200]
    lines = ["Product %d: %s (%s) · %s Rev %s · supplied qty %s%s" % (d["l"].no, d["name"], d["code"], d["r"].number, d["r"].rev, d["qty"],
             " · wants: %s" % (form.get("qty_%d" % d["l"].no) or "").strip()[:40] if (form.get("qty_%d" % d["l"].no) or "").strip() else "") for d in picked]
    text = "Reorder asked on the portal by %s for %s:\n%s%s%s" % (email, ref, "\n".join(lines), "\nSite: " + site if site else "", "\nNote: " + note if note else "")
    with db.Session() as s:
        s.add(db.Message(order_id=o.id, who="desk", text=text))
        s.commit()
    _tell_staff(o, "Reorder asked: %s · %s" % (ref, o.client.name), text)
    items = "\n".join("- %s (%s)%s" % (d["name"], d["code"], ", quantity %s" % (form.get("qty_%d" % d["l"].no) or "").strip()[:40]
                                         if (form.get("qty_%d" % d["l"].no) or "").strip() else "") for d in picked)
    _ack(o, email, ACK_REORDER[0].format(quote=ref), ACK_REORDER[1].format(name=a.name or email, items=items))
    return RedirectResponse("/portal/past?o=%s&sent=1" % ref, status_code=303)


ACK_CHANGES = ("We received your changes: {quote} · {number} Rev {rev}",
               "Dear {name},\n\nWe received the changes you asked for on {when}:\n{notes}\n\nWe will send a revised drawing for your approval. "
               "The drawing as it stands is not approved and will not be manufactured.\n\nRegards,\nEncore Prime Furniture")
ACK_REORDER = ("We received your reorder request: {quote}",
               "Dear {name},\n\nWe received your request to reorder:\n{items}\n\nYour Encore sales person will send you a quotation. "
               "This is not an order until you confirm that quotation.\n\nRegards,\nEncore Prime Furniture")
# Draft wording from the legal review (issue #51, R3); Kushal may adjust it after the lawyer.


def _ack(o, to, subject, body):
    """An acknowledgement to the client who asked (issue #51 R3), from info@. Sent once, when the request is made; a failed
    email never stops the request, and the order's history says whether it went."""
    try:
        issuing.send([to], [], subject, body)
        text = "Acknowledgement emailed to %s: %s" % (to, subject)
    except RuntimeError as e:
        text = "The acknowledgement to %s could not be emailed (%s); the request itself is recorded." % (to, e)
    with db.Session() as s:
        s.add(db.Message(order_id=o.id, who="desk", text=text)); s.commit()


def _now_text():
    import datetime as _d
    return _d.datetime.now(_d.timezone(_d.timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %H:%M")


def _tell_staff(o, subject, body):
    """Tells the order's sales person (and info@) by email. Best effort: the message on the order is the record."""
    to = [issuing.COMPANY]
    with db.Session() as s:
        u = s.scalar(select(db.User).where(db.User.name == o.salesperson, db.User.active == True)) if o.salesperson else None   # noqa: E712
        if u and u.email not in to:
            to.append(u.email)
    try:
        issuing.send(to, [], subject, body + "\n\nOpen the order in the drawing desk: /orders/%s/issue" % o.source_ref)
    except RuntimeError:
        pass


# ---- one order's drawings, one drawing ---------------------------------------------------------------------------------

@router.get("/orders/{ref}", response_class=HTMLResponse)
def order_set(request: Request, ref: str, done: str = ""):
    email = current(request)
    o, a = _access(email, ref)
    ds = drawings(o)
    return _set_page(request, email, o, a, ds, done=done)


def _set_page(request, email, o, a, ds, done="", form=None, error="", code_sent=False, picked=()):
    """The order page (issue #85: built for 100 drawings): progress, search, filter chips, area groups, tick and approve."""
    from .portalreq import of_order, STATUS                             # issue #47: the client's add / remove requests
    todo = being_drawn(o, ds)
    return page(request, "set.html", email, o=o, a=a, ds=ds, sum=summary(ds), done=done, form=form or {}, error=error, code_sent=code_sent, tab="orders",
                reqs=of_order(o.id), req_status=STATUS, todo=todo, groups=groups(ds, todo), picked=set(picked))


def _approve(request, o, a, picked, name, designation):
    """Records a code-verified portal approval for each drawing picked, emails the client the confirmation with the
    approved PDF, and moves the order on when every drawing is approved."""
    desk = _desk()
    ip, today, done, pdfs = _ip(request), datetime.date.today().isoformat(), [], []
    with db.Session() as s:
        for d in picked:
            r = d["r"]
            pdf = storage.local(r.files.get("pdf"))
            s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="approved", name=name[:200], designation=designation[:200], email=a.email,
                              how="portal", on=today, pdf_sha256=issuing.fingerprint(pdf) if pdf else "", ip=ip[:60], code_verified=True,
                              by="Portal: %s" % a.email))
            done.append("%s Rev %s" % (r.number, r.rev))
            if pdf:
                pdfs.append(pdf)
        s.add(db.Message(order_id=o.id, who="desk", text="Approved on the portal by %s (%s, %s), code verified: %s." % (name, designation or "no designation", a.email, ", ".join(done))))
        s.commit()
    storage.log(a.email, "portal", "approve", ", ".join(done), "", o.id, ip)
    att = None
    if pdfs:
        att = issuing.combine(pdfs, os.path.join(engines.FILES, str(o.id), "approved",
                                                 "approved-%s.pdf" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    try:
        issuing.send([a.email], [], "Drawings approved: %s · %s" % (o.source_ref, o.client.name),
                     "Dear %s,\n\nThank you. This confirms your approval for manufacturing, given on the Encore client portal on %s:\n\n%s\n\n"
                     "Approved by: %s%s (%s), with a one-time code sent to this email. The approved drawings are attached.\n\n"
                     "Regards,\nEncore Prime Furniture" % (name, datetime.datetime.now(IST).strftime("%d %b %Y, %H:%M IST"), "\n".join(done),
                                                           name, ", " + designation if designation else "", a.email),
                     att, "Approved-%s.pdf" % storage.safe(o.source_ref) if att else None)
    except RuntimeError:
        pass
    _tell_staff(o, "Approved on the portal: %s · %s" % (o.source_ref, o.client.name), "%s (%s) approved: %s" % (name, a.email, ", ".join(done)))
    desk.all_approved_step(o.source_ref, "Portal: %s" % a.email, "approved on the client portal")
    return done


@router.post("/orders/{ref}/approve", response_class=HTMLResponse)
async def approve_set(request: Request, ref: str):
    """Approve the remaining (waiting) drawings: name, designation, a one-time code to this email, the tick.
    Button "Email me a code" sends the code and shows the same form again, filled in."""
    email = current(request)
    o, a = _access(email, ref)
    form = await request.form()
    f = {k: (form.get(k) or "").strip() for k in ("name", "designation", "code", "agree", "do")}
    ds = drawings(o)
    rid = int(form.get("rid") or 0)
    picks = {int(x) for x in form.getlist("pick") if str(x).isdigit()}   # issue #85: the ticked drawings; none ticked = all waiting
    waiting = [d for d in ds if d["status"] == "waiting" and (not rid or d["r"].id == rid) and (rid or not picks or d["r"].id in picks)]
    back = ((lambda err="", sent=False: page(request, "drawing.html", email, o=o, a=a, ds=ds, sum=summary(ds), done="", form=f,
                                             error=err, code_sent=sent, tab="orders", **_drawing_ctx(ds, rid))) if rid else
            (lambda err="", sent=False: _set_page(request, email, o, a, ds, form=f, error=err, code_sent=sent, picked=picks)))
    if not a.can_approve:
        return back("Your email can view and comment on these drawings, but not approve them. Ask your Encore sales person if you should approve.")
    if not waiting:
        return back("There is nothing waiting for your approval.")
    if f["do"] == "code":
        err = new_code(email, "approve", o.id, _ip(request))
        return back(err, sent=not err)
    if not f["name"]:
        return back("Enter your name.")
    if not f["agree"]:
        return back("Tick the box to confirm your approval.")
    err = check_code(email, "approve", f["code"], o.id)
    if err:
        return back(err)
    done = _approve(request, o, a, waiting, f["name"], f["designation"])
    return RedirectResponse("/portal/orders/%s%s?done=approved-%d" % (ref, "/d/%d" % rid if rid else "", len(done)), status_code=303)


def _drawing_ctx(ds, rid):
    i = next((n for n, d in enumerate(ds) if d["r"].id == rid), None)
    if i is None:
        raise HTTPException(404, "Not found")
    d = ds[i]
    pages = len(storage.preview_paths(d["r"].files.get("pdf"))) or 1
    with db.Session() as s:
        marks = list(s.scalars(select(db.Mark).where(db.Mark.line_id == d["l"].id, db.Mark.shown.like("r%d%%" % d["r"].id), db.Mark.by.like("Client:%")).order_by(db.Mark.no)))
    return dict(d=d, prev=ds[i - 1] if i > 0 else None, next=ds[i + 1] if i + 1 < len(ds) else None, pages=pages, marks=marks, sheet=1)


@router.get("/orders/{ref}/d/{rid}", response_class=HTMLResponse)
def drawing(request: Request, ref: str, rid: int, done: str = "", s: int = 1):
    email = current(request)
    o, a = _access(email, ref)
    ds = drawings(o)
    cx = _drawing_ctx(ds, rid)
    cx["sheet"] = max(1, min(s, cx["pages"]))
    return page(request, "drawing.html", email, o=o, a=a, ds=ds, done=done, form={}, error="", code_sent=False, tab="orders", **cx)


@router.post("/orders/{ref}/d/{rid}/changes", response_class=HTMLResponse)
async def ask_changes(request: Request, ref: str, rid: int):
    """Ask for a change on one drawing: numbered marks on the sheet with a note each, anything else, an attachment.
    Anyone on the list may ask (view and comment). The team is told straight away; the drawing comes back as a new revision."""
    email = current(request)
    o, a = _access(email, ref)
    ds = drawings(o)
    cx = _drawing_ctx(ds, rid)
    d = cx["d"]
    form = await request.form()
    if d["status"] == "approved":
        return page(request, "drawing.html", email, o=o, a=a, ds=ds, done="", form={}, error="This drawing is approved and locked. Contact your Encore sales person for any change.",
                    code_sent=False, tab="orders", **cx)
    try:
        marks = [m for m in json.loads(form.get("marks") or "[]") if isinstance(m, dict)][:30]
    except ValueError:
        marks = []
    note, who = (form.get("note") or "").strip()[:2000], (form.get("name") or "").strip()[:200] or a.name or email
    marks = [dict(x=float(m.get("x", -1)), y=float(m.get("y", -1)), sheet=int(m.get("sheet", 1) or 1), text=str(m.get("text", "")).strip()[:300])
             for m in marks if isinstance(m.get("x"), (int, float)) and isinstance(m.get("y"), (int, float))]
    marks = [m for m in marks if 0 <= m["x"] <= 100 and 0 <= m["y"] <= 100]
    if not note and not any(m["text"] for m in marks):
        return page(request, "drawing.html", email, o=o, a=a, ds=ds, done="", form={}, error="Say what you would like changed: a note on a mark, or under Anything else.",
                    code_sent=False, tab="orders", **cx)
    up = form.get("file")
    proof = proof_name = ""
    if up is not None and getattr(up, "filename", ""):
        data = await up.read()
        if data and len(data) <= 15 * 1024 * 1024:
            safe = re.sub(r"[^\w.\-]+", "_", up.filename)[-80:]
            proof = os.path.join(engines.FILES, str(o.id), "approvals", "%d-client-%d-%s" % (rid, int(datetime.datetime.now().timestamp()), safe))
            os.makedirs(os.path.dirname(proof), exist_ok=True)
            open(proof, "wb").write(data)
            proof_name = up.filename[:200]
    l, r = d["l"], d["r"]
    with db.Session() as s:
        start = max([m.no for m in s.scalars(select(db.Mark).where(db.Mark.line_id == l.id))], default=0)
        texts = []
        for n, m in enumerate(marks, start + 1):
            s.add(db.Mark(line_id=l.id, shown="r%d" % r.id if m["sheet"] == 1 else "r%ds%d" % (r.id, m["sheet"]), no=n, x=round(m["x"], 2), y=round(m["y"], 2),
                          label=m["text"] or "(no note)", by="Client: %s" % who[:100]))
            texts.append("[%d] %s" % (n, m["text"] or "(no note)"))
        full = "; ".join(texts + ([note] if note else []))
        s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="changes", name=who, email=email, how="portal", on=datetime.date.today().isoformat(),
                          note=full[:2000], proof=proof, proof_name=proof_name, ip=_ip(request)[:60], by="Portal: %s" % email))
        s.add(db.Message(order_id=o.id, who="desk", text="Changes asked on the portal by %s (%s) on %s Rev %s: %s" % (who, email, r.number, r.rev, full)))
        s.commit()
    if proof:
        with db.Session() as s:
            folder = _desk()._folder(s, o.client_id)
        if folder:
            storage.keep(proof, "%s/Other/Approvals/%s" % (folder, os.path.basename(proof)))
    _tell_staff(o, "Changes asked on the portal: %s · %s" % (o.source_ref, o.client.name),
                "%s (%s) asked for changes on Product %d, %s Rev %s:\n%s" % (who, email, l.no, r.number, r.rev, full))
    numbered = "\n".join(texts + (["Other: " + note] if note else []))
    _ack(o, email, ACK_CHANGES[0].format(quote=o.source_ref, number=r.number, rev=r.rev),
         ACK_CHANGES[1].format(name=who, when=_now_text(), notes=numbered))
    return RedirectResponse("/portal/orders/%s/d/%d?done=changes" % (ref, rid), status_code=303)


# ---- files (always through the access check, signed links, logged) --------------------------------------------------

def _rev(o, rid):
    d = next((d for d in drawings(o) if d["r"].id == rid), None)
    if d is None:
        raise HTTPException(404, "Not found")
    return d


@router.get("/orders/{ref}/d/{rid}/sheet/{n}")
def sheet(request: Request, ref: str, rid: int, n: int):
    email = current(request)
    o, a = _access(email, ref)
    d = _rev(o, rid)
    pv = storage.preview_paths(d["r"].files.get("pdf"))
    if not 1 <= n <= len(pv):
        raise HTTPException(404, "Not found")
    return storage.serve(request, pv[n - 1], "%s-Rev%s-sheet%d.png" % (d["r"].number, d["r"].rev, n), who=email, side="portal", order_id=o.id, inline_name=False)


@router.get("/orders/{ref}/d/{rid}/pdf")
def drawing_pdf(request: Request, ref: str, rid: int, download: int = 0):
    email = current(request)
    o, a = _access(email, ref)
    d = _rev(o, rid)
    return storage.serve(request, d["r"].files.get("pdf"), "%s-Rev%s.pdf" % (d["r"].number, d["r"].rev), download=bool(download), who=email, side="portal", order_id=o.id)


@router.get("/orders/{ref}/set.pdf")
def set_pdf(request: Request, ref: str):
    email = current(request)
    o, a = _access(email, ref)
    got = _desk()._combined(o)
    if not got:
        raise HTTPException(404, "Nothing has been issued yet")
    return storage.serve(request, got[0], got[1], download=True, who=email, side="portal", order_id=o.id)


def personal_body(body, link):
    """The drawings email with the recipient's own portal link added."""
    return body.rstrip() + "\n\nSee the drawings, ask for changes or approve them online:\n%s\n(This link is for you only. Approving asks for a one-time code sent to your email.)" % link


def general_body(body, base):
    return body.rstrip() + "\n\nThe drawings are also on our client portal: %s/portal (only the emails we have listed for this order can sign in)." % base.rstrip("/")

