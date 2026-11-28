"""Screen 8: quotation changes. Each time the quotation is read from Odoo (the webhook, opening the project, "Check Odoo
now", the nightly check) its lines are compared with the last saved version. A difference saves a new version and lists
each change in the inbox with what it means for that product's drawing at its stage (UI_BRIEF, quotation change rules):

| Drawing stage      | Added                                | Dropped                              | Qty / spec changed                          |
| Not drawn          | Specify                              | Remove                               | Use new values                              |
| Drawn, not issued  | Specify                              | Archive                              | Regenerate, same rev                        |
| Issued to client   | Specify, issue as addition           | Mark Cancelled, reissue set          | New revision (ask once)                     |
| Client approved    | Specify, needs client approval       | Cancel, admin permission             | Locked: admin permission, new rev, re-approval |
| In production      | Separate production set              | Stop alert to production + admin     | Stop alert, redo production drawings + BOM  |

Products are tied to the Odoo quotation line id: product numbers and drawing serials never shift and are never reused.
Price-only changes are ignored. Issue and production are locked while a change is open. Lines that need no drawing
(bought-out, services, installation, delivery) follow the no-drawing list and never open a change.
Routes are kept here, apart from app.py; app.py includes this router."""
import datetime, os, secrets
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select
from . import db, auth, engines
from .app import T, User, ctx, load, SOURCE, product_key, _next_no, _new_line, _draw_lines, _locked, _permitted, IST
from .reading import read_line

router = APIRouter()
ACTIONS = {
    ("added", "not drawn"): "specify like any new product", ("added", "drawn"): "specify like any new product",
    ("added", "issued"): "specify, issue as an addition", ("added", "approved"): "specify; it needs the client's approval",
    ("added", "production"): "specify; it gets a separate production set",
    ("dropped", "not drawn"): "remove it from the drawings", ("dropped", "drawn"): "archive the preview",
    ("dropped", "issued"): "mark Cancelled, reissue the combined set", ("dropped", "approved"): "cancel it: needs admin permission",
    ("dropped", "production"): "stop alert sent to production + admins; cancel it (admin)",
    ("change", "not drawn"): "use the new values", ("change", "drawn"): "regenerate, same revision",
    ("change", "issued"): "new revision", ("change", "approved"): "locked: admin permission, new revision, client re-approval",
    ("change", "production"): "stop alert sent to production + admins; redo production drawings and BOM (admin)",
}


def snapshot(src):
    return [dict(ref=str(getattr(l, "line_ref", "") or ""), name=l.name, description=l.description or "", qty=float(l.qty or 0),
                 price=float(getattr(l, "price", 0) or 0), no_drawing=l.no_drawing or "") for l in src.lines]


def diff(old, new):
    """Changes between two saved line lists. -> [dict(kind, ref, name, before, after)]. Lines without an id are matched by
    position (older saved versions). Lines that need no drawing never make a change (they follow the no-drawing list)."""
    key = lambda x, i: x["ref"] or "pos-%d" % i
    a = {key(x, i): x for i, x in enumerate(old)}
    b = {key(x, i): x for i, x in enumerate(new)}
    out = []
    for k, x in b.items():
        if k not in a:
            if not x["no_drawing"]:
                out.append(dict(kind="added", ref=x["ref"], name=x["name"], before={}, after=x))
            continue
        y = a[k]
        if x["no_drawing"] and y["no_drawing"]:
            continue
        if x["no_drawing"] and not y["no_drawing"]:                          # became a bought-out / service line
            out.append(dict(kind="dropped", ref=x["ref"], name=y["name"], before=y, after={})); continue
        if y["no_drawing"] and not x["no_drawing"]:
            out.append(dict(kind="added", ref=x["ref"], name=x["name"], before={}, after=x)); continue
        if (x["name"], x["description"]) != (y["name"], y["description"]):
            out.append(dict(kind="spec", ref=x["ref"], name=x["name"], before=y, after=x))
        elif x["qty"] != y["qty"]:
            out.append(dict(kind="qty", ref=x["ref"], name=x["name"], before=y, after=x))
        elif x["price"] != y["price"]:
            out.append(dict(kind="price", ref=x["ref"], name=x["name"], before=y, after=x))
    for k, y in a.items():
        if k not in b and not y["no_drawing"]:
            out.append(dict(kind="dropped", ref=y["ref"], name=y["name"], before=y, after={}))
    return out


def stage_of_line(o, l):
    """not drawn | drawn | issued | approved | production, for one product (None = no product yet: not drawn)."""
    if l is None or (not l.drawing and not l.revisions):
        return "not drawn"
    if not l.revisions:
        return "drawn"
    if o.stage in ("production", "completed"):
        return "production"
    if _locked(o, l) or o.stage == "approved":
        return "approved"
    return "issued"


def action(kind, stage):
    return ACTIONS.get(("change" if kind in ("qty", "spec") else kind, stage), "")


def _tie(s, o, src_lines):
    """Old data: products read before they were tied to quotation lines are tied now, by their reading order
    (Product N was the N-th line that gets a drawing), else by the same product name."""
    drawn = [x for x in src_lines if not x.no_drawing]
    for l in s.scalars(select(db.OrderLine).where(db.OrderLine.order_id == o.id)):
        if l.source_line:
            continue
        x = next((x for x in drawn if x.no == l.no and product_key(x.name) == product_key(l.name)), None) \
            or next((x for x in drawn if product_key(x.name) == product_key(l.name)), None)
        if x is not None and getattr(x, "line_ref", ""):
            l.source_line = x.line_ref


def check(ref, why="check now"):
    """Reads the quotation again and records what changed. -> (version n, [new QuoteChange ids]). Raises KeyError when the
    source no longer has the order. Odoo is never written to."""
    src = SOURCE.get_order(ref)
    o = load(ref)
    snap = snapshot(src)
    new_ids = []
    with db.Session() as s:
        order = s.get(db.Order, o.id)
        order.checked_at = db.now()
        _tie(s, o, src.lines)
        last = s.scalars(select(db.QuoteVersion).where(db.QuoteVersion.order_id == o.id).order_by(db.QuoteVersion.n.desc())).first()
        if last is None:                                                  # the first look: this is version 1, nothing to compare
            s.add(db.QuoteVersion(order_id=o.id, n=1, lines=snap, why="first read"))
            s.commit()
            return 1, []
        changes = diff(last.lines, snap)
        if not changes:
            s.commit()
            return last.n, []
        n = last.n + 1
        s.add(db.QuoteVersion(order_id=o.id, n=n, lines=snap, why=why))
        by_ref = {l.source_line: l for l in o.lines if l.source_line}
        alerts = []
        for c in changes:
            l = by_ref.get(c["ref"])
            st = stage_of_line(o, l)
            qc = db.QuoteChange(order_id=o.id, version=n, kind=c["kind"], line_ref=c["ref"], line_no=l.no if l else 0, name=c["name"],
                                before=c["before"], after=c["after"], stage=st)
            if c["kind"] == "price":
                qc.status, qc.handled_by, qc.handled_at, qc.note = "ignored", "desk", db.now(), "Price only: drawings not affected"
            s.add(qc); s.flush(); new_ids.append(qc.id)
            if st == "production" and c["kind"] != "price":
                alerts.append(qc)
        s.add(db.Message(order_id=o.id, who="desk", text="Odoo quotation version %d found (%s): %d difference%s." % (n, why, len(changes), "" if len(changes) == 1 else "s")))
        for qc in alerts:                                                 # decision 12: stop alert when production has started
            _stop_alert(s, order, qc)
        s.commit()
    return n, new_ids


def _stop_alert(s, order, qc):
    text = "STOP: the quotation changed after production started. %s (%s). Hold this product until the production drawings are redone." % (
        _title(qc), order.source_ref)
    s.add(db.Message(order_id=order.id, who="desk", text=text))
    names = {u.name for u in s.scalars(select(db.User).where(db.User.active == True)) if u.role in ("admin", "production")  # noqa: E712
             or (u.email or "").lower() in auth.SECONDARY}
    if order.salesperson:
        names.add(order.salesperson)
    for name in sorted(names):
        s.add(db.Task(assignee=name, order_ref=order.source_ref, line_no=qc.line_no, text=text, source="quotestop:%d" % qc.id))
    qc.note = "Stop alert sent to %s" % ", ".join(sorted(names))


def _title(qc):
    what = {"added": "Added", "dropped": "Dropped", "qty": "Qty changed", "spec": "Spec changed", "price": "Price only"}[qc.kind]
    who = ("Product %d · " % qc.line_no if qc.line_no else "") + qc.name.split("(")[0].strip()
    if qc.kind == "qty":
        return "%s: %s · qty %g -> %g" % (what, who, qc.before.get("qty", 0), qc.after.get("qty", 0))
    return "%s: %s" % (what, who)


def open_changes(order_id):
    with db.Session() as s:
        return s.scalars(select(db.QuoteChange).where(db.QuoteChange.order_id == order_id, db.QuoteChange.status == "open")).all()


def lock_reason(order_id):
    """Issue and production are locked while quotation changes are open. -> the reason in plain words, or ''."""
    n = len(open_changes(order_id))
    return "Handle the %d quotation change%s first (Quotation changes): drawings and quotation must match." % (n, "" if n == 1 else "s") if n else ""


def maybe_check(ref, minutes=10):
    """When a project is opened: compares with Odoo if it was not checked in the last few minutes. Never stops the page."""
    try:
        with db.Session() as s:
            o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
            if o is None or o.source != "odoo":
                return
            if o.checked_at and (db.now() - o.checked_at.replace(tzinfo=o.checked_at.tzinfo or datetime.timezone.utc)).total_seconds() < minutes * 60:
                return
        check(ref, "project opened")
    except Exception:
        pass


# ---- the inbox ---------------------------------------------------------------------------------------------------------
@router.get("/orders/{ref}/changes", response_class=HTMLResponse)
def inbox(request: Request, ref: str, error: str = "", user=User):
    o = load(ref)
    with db.Session() as s:
        ch = s.scalars(select(db.QuoteChange).where(db.QuoteChange.order_id == o.id).order_by(db.QuoteChange.id.desc())).all()
        versions = s.scalars(select(db.QuoteVersion).where(db.QuoteVersion.order_id == o.id).order_by(db.QuoteVersion.n.desc())).all()
        added = list(s.scalars(select(db.AddedLine).where(db.AddedLine.order_id == o.id)))
    lines = {l.no: l for l in o.lines}
    rows = []
    for c in ch:
        l = lines.get(c.line_no)
        rows.append(dict(c=c, title=_title(c), action=action(c.kind, c.stage), l=l, permitted=bool(l and _permitted(o, l)),
                         can_admin=user.role == "admin"))
    rows.sort(key=lambda r: r["c"].status != "open")                     # open ones first, newest first within each
    opened = [r for r in rows if r["c"].status == "open"]
    hist = [(v.at, "Odoo version %d found · %s" % (v.n, v.why)) for v in versions]
    hist += [(c.handled_at, "%s · %s · %s" % (_title(c), "handled" if c.status == "handled" else "ignored", c.handled_by)) for c in ch if c.handled_at and c.handled_by != "desk"]
    hist += [(c.at, "Stop alert sent · %s" % _title(c)) for c in ch if c.note.startswith("Stop alert")]
    hist.sort(key=lambda x: x[0].replace(tzinfo=None), reverse=True)
    return T.TemplateResponse(request, "changes.html", ctx(user, o=o, rows=rows, opened=opened, versions=versions, hist=hist[:40], added=added, error=error,
                                                            last=versions[0] if versions else None, prev=versions[1] if len(versions) > 1 else None, nav="projects", tab="changes"))


def _back(ref, error=""):
    from urllib.parse import quote
    return RedirectResponse("/orders/%s/changes%s" % (ref, "?error=" + quote(error) if error else ""), status_code=303)


@router.post("/orders/{ref}/changes/check")
def check_now(ref: str, user=User):
    try:
        check(ref, "check now")
    except KeyError:
        return _back(ref, "The quotation was not found in Odoo any more")
    except Exception as e:
        return _back(ref, "Odoo could not be read: %s" % str(e)[:200])
    return _back(ref)


def _handled(s, qc, user, note=""):
    qc.status, qc.handled_by, qc.handled_at = "handled", user.name, db.now()
    if note:
        qc.note = (qc.note + " · " if qc.note else "") + note


@router.post("/orders/{ref}/changes/{cid}/{what}")
def handle(ref: str, cid: int, what: str, user=User):
    """add | remove | cancel | update | ask | ignore: does what the change rules say for the product's stage."""
    o = load(ref)
    with db.Session() as s:
        qc = s.get(db.QuoteChange, cid)
        if qc is None or qc.order_id != o.id or qc.status != "open":
            raise HTTPException(400, "This change is already handled")
        l = next((x for x in o.lines if x.no == qc.line_no), None) if qc.line_no else None
        stage = stage_of_line(o, l)                                       # the stage now (it may have moved since the change came)
        admin = user.role == "admin"
        if what == "add" and qc.kind == "added":
            no = _next_no(s, o.id)
            a = qc.after
            nl = _new_line(s, o.id, no, a.get("name", ""), a.get("description", ""), a.get("qty", 1))
            nl.source_line = qc.line_ref
            qc.line_no = no
            _handled(s, qc, user, "Added as Product %d" % no)
            s.add(db.Message(order_id=o.id, who="desk", text="Product %d (%s) added from the quotation by %s. %s." % (no, a.get("name", ""), user.name, action("added", stage).capitalize())))
        elif what == "remove" and qc.kind == "dropped" and stage in ("not drawn", "drawn"):
            if l is not None:
                line = s.get(db.OrderLine, l.id)
                s.add(db.NoDrawingLine(order_id=o.id, name=line.name, qty=line.qty, description=line.description, reason="Dropped from the quotation", no=line.no, by=user.name))
                s.delete(line)
            _handled(s, qc, user, "Moved to No drawing needed" + ("" if stage == "not drawn" else " (the preview is not kept)"))
            s.add(db.Message(order_id=o.id, who="desk", text="%s: removed from the drawings by %s (dropped from the quotation)." % (_title(qc), user.name)))
        elif what == "cancel" and qc.kind == "dropped" and l is not None and stage in ("issued", "approved", "production"):
            if stage in ("approved", "production") and not (admin or _permitted(o, l)):
                raise HTTPException(403, "Cancelling a client-approved drawing needs Kushal's permission: ask for it first")
            line = s.get(db.OrderLine, l.id)
            line.cancelled_by, line.cancelled_at = user.name, db.now()
            r = s.get(db.Revision, l.last_rev.id)
            stamped = cancel_stamp(r, "Q-v%d" % qc.version)
            if stamped:
                r.files = dict(r.files, cancelled_pdf=stamped)
            _handled(s, qc, user, "Marked CANCELLED; serial %s retired, left out of the combined set" % r.number.rsplit("-", 1)[-1])
            s.add(db.Message(order_id=o.id, who="desk", text="%s Rev %s marked CANCELLED by %s (dropped from the quotation, version %d). Its serial is retired and never reused; "
                             "the combined PDF and sheet numbers are made again without it. Reissue the set to the client." % (r.number, r.rev, user.name, qc.version)))
        elif what == "update" and qc.kind in ("qty", "spec") and l is not None:
            if stage in ("approved", "production") and not (admin or _permitted(o, l)):
                raise HTTPException(403, "This drawing is client-approved: ask for Kushal's permission first")
            line = s.get(db.OrderLine, l.id)
            a = qc.after
            line.name, line.description, line.qty = a.get("name", line.name), a.get("description", line.description), a.get("qty", line.qty)
            what_ = "qty %g -> %g" % (qc.before.get("qty", 0), a.get("qty", 0)) if qc.kind == "qty" else "specification from the quotation"
            s.add(db.Change(line_id=line.id, typed="Product %d: quotation version %d, %s" % (line.no, qc.version, what_), understood="quotation version %d: %s" % (qc.version, what_),
                            values={}, status="confirmed", by=user.name))
            _handled(s, qc, user, {"not drawn": "New values used", "drawn": "Regenerated, same revision", "issued": "Redrawn; issuing makes the next revision",
                                   "approved": "Redrawn with permission; the new revision needs the client's approval again",
                                   "production": "Redrawn; issue the new revision, then redo the production drawings and BOM"}[stage])
            s.add(db.Message(order_id=o.id, who="desk", text="%s: updated by %s (%s)." % (_title(qc), user.name, qc.note)))
            s.commit()
            o2 = load(ref)
            if stage != "not drawn":
                _draw_lines(o2, s, user.name, only={line.no})
        elif what == "ask" and l is not None and stage in ("approved", "production"):
            s.add(db.ChangeRequest(kind="after_approval", order_ref=ref, line_no=l.no, product="Product %d: %s" % (l.no, l.name.split("(")[0].strip()),
                                   text="Quotation version %d: %s" % (qc.version, _title(qc)), asked_by=user.name))
            qc.note = (qc.note + " · " if qc.note else "") + "Permission asked by %s" % user.name
        elif what == "ignore" and admin:
            qc.status, qc.handled_by, qc.handled_at = "ignored", user.name, db.now()
        else:
            raise HTTPException(400, "That cannot be done for this change at its stage")
        s.commit()
    return _back(ref)


@router.post("/orders/{ref}/changes/notify-sales")
def notify_sales(ref: str, user=User):
    """Quotation needs update (drawing -> sales): products added on the desk that the quotation does not have yet."""
    o = load(ref)
    with db.Session() as s:
        added = list(s.scalars(select(db.AddedLine).where(db.AddedLine.order_id == o.id)))
        if not added:
            raise HTTPException(400, "Nothing to tell sales: the quotation matches")
        text = "Quotation needs update (%s): %s. Please add %s to the quotation in Odoo." % (
            ref, "; ".join("Product %d · %s" % (a.no, a.name) for a in added), "them" if len(added) > 1 else "it")
        s.add(db.Task(assignee=o.salesperson or "Sales", order_ref=ref, text=text, source="quoteupdate:%d" % o.id))
        s.add(db.Message(order_id=o.id, who="desk", text="Salesperson %s told by %s: %s" % (o.salesperson or "(none set)", user.name, text)))
        s.commit()
    return _back(ref)


def cancel_stamp(r, version):
    """A copy of the issued PDF with a CANCELLED stamp (date and quotation version) on every sheet. The issued PDF itself is
    never changed; the copy goes with the old revisions."""
    import pymupdf
    src = (r.files or {}).get("pdf")
    if not src or not os.path.exists(src):
        return ""
    out = src[:-4] + "-CANCELLED.pdf"
    when = datetime.datetime.now(IST).strftime("%d %b %Y").upper()
    with pymupdf.open(src) as doc:
        for pg in doc:
            w, h = pg.rect.width, pg.rect.height
            box = pymupdf.Rect(w * 0.25, h * 0.38, w * 0.75, h * 0.62)
            pg.draw_rect(box, color=(0.73, 0.11, 0.11), width=4)
            pg.insert_textbox(box + (0, h * 0.03, 0, 0), "CANCELLED", fontsize=min(w, h) / 9, fontname="hebo", color=(0.73, 0.11, 0.11), align=1)
            pg.insert_textbox(box + (0, h * 0.15, 0, 0), "%s · quotation %s" % (when, version), fontsize=min(w, h) / 40, fontname="helv", color=(0.73, 0.11, 0.11), align=1)
        doc.save(out)
    return out


# ---- Odoo webhook (decision 2) -----------------------------------------------------------------------------------------
def _token_ok(request):
    want = os.environ.get("ODOO_WEBHOOK_TOKEN", "")
    got = request.query_params.get("token", "") or request.headers.get("x-desk-token", "")
    return bool(want) and secrets.compare_digest(want, got)


@router.post("/webhooks/odoo")
async def odoo_webhook(request: Request):
    """Odoo's automation rule on the Sales Order posts here when its lines, quantities or state change. The body is the
    record as Odoo sends it (its "name" is the quotation number, e.g. Q-2026-11-506). Only projects already on the desk are
    checked; anything else is acknowledged and left alone. Needs ODOO_WEBHOOK_TOKEN (in the URL as ?token=...)."""
    if not os.environ.get("ODOO_WEBHOOK_TOKEN"):
        return JSONResponse({"ok": False, "error": "ODOO_WEBHOOK_TOKEN is not set on the desk"}, status_code=503)
    if not _token_ok(request):
        return JSONResponse({"ok": False, "error": "wrong token"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ref = str((body or {}).get("name") or request.query_params.get("ref") or "").strip()
    if not ref:
        return JSONResponse({"ok": False, "error": "no quotation number (name) in the body"}, status_code=400)
    with db.Session() as s:
        known = s.scalar(select(db.Order.id).where(db.Order.source_ref == ref)) is not None
    if not known:
        return JSONResponse({"ok": True, "ref": ref, "project": False})
    try:
        n, new = check(ref, "webhook")
    except KeyError:
        return JSONResponse({"ok": False, "ref": ref, "error": "not found in Odoo"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "ref": ref, "error": str(e)[:200]}, status_code=502)
    return JSONResponse({"ok": True, "ref": ref, "project": True, "version": n, "changes": len(new)})


@router.post("/webhooks/odoo/nightly")
def odoo_nightly(request: Request):
    """The nightly safety net: compares every active Odoo project with its quotation. Same token as the webhook."""
    if not os.environ.get("ODOO_WEBHOOK_TOKEN"):
        return JSONResponse({"ok": False, "error": "ODOO_WEBHOOK_TOKEN is not set on the desk"}, status_code=503)
    if not _token_ok(request):
        return JSONResponse({"ok": False, "error": "wrong token"}, status_code=401)
    from .app import stage_of
    done, failed = 0, []
    with db.Session() as s:
        refs = [o.source_ref for o in s.scalars(select(db.Order).where(db.Order.source == "odoo")) if stage_of(o.steps)[0] != "completed"]
    for ref in refs:
        try:
            check(ref, "nightly"); done += 1
        except Exception as e:
            failed.append("%s: %s" % (ref, str(e)[:80]))
    return JSONResponse({"ok": not failed, "checked": done, "failed": failed})
