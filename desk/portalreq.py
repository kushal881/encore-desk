"""Client requests on the portal to add or remove a product (issue #47).
Portal: on an order's drawing set, "Ask to add a product" (name / description, quantity, note, optional file) and "Ask to
remove a product" (tick products, all or part of the quantity, reason). Only emails on the access list with Approve
rights may ask. The client sees each request with its status (Received / Being handled / Done / Declined) and answer.
Desk: a "Client requests" card on Questions and changes, and a notification for the salesperson, the designers on the
project and the administrators (bell). Staff mark it Being handled, Done (with an answer) or Declined (with a reason); the
client gets the answer by email from info@ and sees it on the portal. Nothing changes by itself: the salesperson updates
the Odoo quotation, and the quotation changes inbox then adds or cancels the product; the card links to that change."""
import datetime, os, re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from . import db, engines, issuing, portal
from .app import User, load

router = APIRouter()
STATUS = dict(received="Received", handling="Being handled", done="Done", declined="Declined")


def of_order(order_id):
    with db.Session() as s:
        return list(s.scalars(select(db.PortalRequest).where(db.PortalRequest.order_id == order_id).order_by(db.PortalRequest.id.desc())))


def linked_change(r):
    """The quotation change that answers this request, once it has come from Odoo (added / dropped after the request)."""
    want = "added" if r.kind == "add" else "dropped"
    with db.Session() as s:
        return s.scalars(select(db.QuoteChange).where(db.QuoteChange.order_id == r.order_id, db.QuoteChange.kind == want,
                                                      db.QuoteChange.at >= r.at).order_by(db.QuoteChange.id)).first()


# ---- portal ------------------------------------------------------------------------------------------------------------
@router.post("/portal/orders/{ref}/request/{kind}")
async def ask(request: Request, ref: str, kind: str):
    email = portal.current(request)
    o, a = portal._access(email, ref)
    if kind not in ("add", "remove"):
        raise HTTPException(404, "Not found")
    if not a.can_approve:
        return RedirectResponse("/portal/orders/%s?done=req-view#requests" % ref, status_code=303)
    form = await request.form()
    note = (form.get("note") or "").strip()[:2000]
    who = (form.get("name") or "").strip()[:200] or a.name or email
    r = db.PortalRequest(order_id=o.id, kind=kind, email=email, name=who, note=note)
    if kind == "add":
        r.product = " ".join((form.get("product") or "").split())[:500]
        r.qty = (form.get("qty") or "").strip()[:40]
        if not r.product:
            return RedirectResponse("/portal/orders/%s?done=req-noname#requests" % ref, status_code=303)
        up = form.get("file")
        if up is not None and getattr(up, "filename", ""):
            data = await up.read()
            if data and len(data) <= 15 * 1024 * 1024:
                safe = re.sub(r"[^\w.\-]+", "_", up.filename)[-80:]
                r.file = os.path.join(engines.FILES, str(o.id), "requests", "%d-%s" % (int(datetime.datetime.now().timestamp()), safe))
                os.makedirs(os.path.dirname(r.file), exist_ok=True)
                open(r.file, "wb").write(data)
                r.file_name = up.filename[:200]
    else:
        ds = {d["l"].no: d for d in portal.drawings(o)}
        picked = []
        for n in form.getlist("no"):
            if n.isdigit() and int(n) in ds:
                d = ds[int(n)]
                q = (form.get("qty_%s" % n) or "").strip()[:20]
                picked.append(dict(no=d["l"].no, name=d["name"], qty=q or "all"))
        if not picked:
            return RedirectResponse("/portal/orders/%s?done=req-none#requests" % ref, status_code=303)
        r.items = picked
    with db.Session() as s:
        s.add(r); s.flush()
        what = ("add %s (qty %s)" % (r.product, r.qty or "not given")) if kind == "add" else "remove " + "; ".join(
            "Product %d %s (%s)" % (x["no"], x["name"], x["qty"]) for x in r.items)
        s.add(db.Message(order_id=o.id, who="desk", text="Client request on the portal by %s (%s): %s%s" % (who, email, what, ". Note: " + note if note else "")))
        s.commit()
    portal._tell_staff(o, "Client asks to %s a product: %s · %s" % (kind, ref, o.client.name),
                       "%s (%s) asks to %s.%s\nThe salesperson updates the Odoo quotation; the drawings follow from the quotation change." % (
                           who, email, what, "\nNote: " + note if note else ""))
    return RedirectResponse("/portal/orders/%s?done=req-sent#requests" % ref, status_code=303)


# ---- desk --------------------------------------------------------------------------------------------------------------
@router.post("/orders/{ref}/requests/{rid}/{what}")
def handle(ref: str, rid: int, what: str, answer: str = Form(""), user=User):
    """handling | done | declined. Done and Declined need an answer, which the client gets by email and on the portal."""
    o = load(ref)
    answer = answer.strip()[:2000]
    with db.Session() as s:
        r = s.get(db.PortalRequest, rid)
        if r is None or r.order_id != o.id or what not in ("handling", "done", "declined"):
            raise HTTPException(404)
        if r.status in ("done", "declined"):
            raise HTTPException(400, "This request is already answered")
        if what in ("done", "declined") and not answer:
            raise HTTPException(400, "Write the answer to the client first")
        r.status, r.handled_by = what, user.name
        if what != "handling":
            r.answer, r.answered_at = answer, db.now()
        s.add(db.Message(order_id=o.id, who="desk", text="Client request %d (%s a product) marked %s by %s%s" % (
            r.id, r.kind, STATUS[what].lower(), user.name, ": " + answer if answer else "")))
        s.commit()
        email, kind, name = r.email, r.kind, r.name
    if what != "handling":
        try:
            issuing.send([email], [], "Your request to %s a product: %s" % (kind, ref),
                         "Dear %s,\n\n%s\n\n%s\n\nRegards,\nEncore Prime Furniture" % (
                             name or "Sir / Madam", "Your request is done." if what == "done" else "We could not make this change.", answer))
        except RuntimeError as e:
            with db.Session() as s:
                s.add(db.Message(order_id=o.id, who="desk", text="The answer to the client could not be emailed: %s" % e)); s.commit()
    return RedirectResponse("/orders/%s/work#requests" % ref, status_code=303)


@router.get("/portal-requests/{rid}/file")
def request_file(rid: int, user=User):
    from fastapi.responses import FileResponse
    with db.Session() as s:
        r = s.get(db.PortalRequest, rid)
    if r is None or not r.file or not os.path.exists(r.file):
        raise HTTPException(404)
    return FileResponse(r.file, filename=r.file_name or os.path.basename(r.file))
