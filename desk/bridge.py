"""Bridge products (issue #73): a drawing made outside the engine, issued while the engine cannot draw a product yet.
Clients and production never wait for an engine fix.

- On a product the engine cannot draw (or one with an engine request), a designer or administrator uploads the drawing made
  outside the engine: PDF (needed), DXF and a BOM / cut list (optional). No bridge without an engine request: an existing
  request for the product is linked, or a new one is made from the text typed.
- The upload becomes the product's preview (engine version "BRIDGE"). It then goes through the same flow as an engine
  drawing: viewer and markers (no AI change panel: changes are made outside and uploaded again as a new version), issue
  with the normal drawing number and revision, client approval, production, combined PDF and storage.
- Engine checks cannot run on it, so it can be issued only after an administrator ticks "Checked by hand (clash, sizes,
  true view)" on that upload; a new upload needs the tick again.
- Issued copies get Encore's drawing number and revision in a box at the bottom right of every sheet; the client sees a
  normal Encore drawing. Production copies carry a red "BRIDGE - made outside the engine, check manually" band.
- The BOM engine leaves bridge products out and says so; the BOM uploaded with the bridge drawing is listed instead.
- Redraw on the engine: once an approved engine version can draw it, the engine drawing becomes the product's preview
  and is issued as the next revision; BRIDGE is cleared.
Routes are kept here, apart from app.py; app.py includes this router at its end."""
import datetime, os, shutil
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from . import auth, db, engines
from .app import User

router = APIRouter()
BRIDGE = "BRIDGE"
CHECK = "Checked by hand (clash, sizes, true view)"


def is_bridge(l):
    """The product's current drawing is a bridge drawing."""
    d = getattr(l, "drawing", None)
    return bool(d is not None and d.engine_version == BRIDGE)


def info(d):
    """The bridge record of a drawing or revision ({} when it is an engine drawing)."""
    return dict((getattr(d, "spec", None) or {}).get("bridge") or {}) if d is not None else {}


def checked(d):
    return bool(info(d).get("checked_by"))


def _may_upload(user):
    return user.role in ("admin", "admin2", "drawing", "coordinator") or auth.level(user) in ("admin", "admin2")


def _is_admin(user):
    return auth.level(user) in ("admin", "admin2")


def _png(pdf, out):
    import pymupdf
    with pymupdf.open(pdf) as doc:
        doc[0].get_pixmap(dpi=110).save(out)
        return len(doc)


def stamp_issue(src, out, number, rev, date):
    """A copy of the uploaded PDF with Encore's drawing number and revision at the bottom right of every sheet."""
    import pymupdf
    with pymupdf.open(src) as doc:
        n = len(doc)
        for i, page in enumerate(doc, 1):
            r = page.rect
            w, h = min(250, r.width * 0.45), 42
            box = pymupdf.Rect(r.x1 - w - 8, r.y1 - h - 8, r.x1 - 8, r.y1 - 8)
            page.draw_rect(box, color=(0.1, 0.1, 0.1), fill=(1, 1, 1), width=0.8, overlay=True)
            page.insert_textbox(box + (5, 3, -5, -2), "ENCORE PRIME FURNITURE\nDrg No. %s   Rev %s\n%s   Sheet %d of %d" % (number, rev, date, i, n),
                                fontsize=7.5, fontname="helv", color=(0, 0, 0), overlay=True)
        doc.save(out)
    return n


def stamp_production(src, out):
    """A copy with a red band on every sheet: production must check a drawing the engine did not make."""
    import pymupdf
    with pymupdf.open(src) as doc:
        for page in doc:
            r = page.rect
            band = pymupdf.Rect(r.x0 + 8, r.y0 + 8, r.x1 - 8, r.y0 + 30)
            page.draw_rect(band, color=(0.72, 0.11, 0.11), fill=(0.72, 0.11, 0.11), overlay=True)
            page.insert_text((band.x0 + 8, band.y1 - 7), "BRIDGE - made outside the engine, check manually", fontsize=11, fontname="hebo",
                             color=(1, 1, 1), overlay=True)
        doc.save(out)
        return len(doc)


def issue_files(d, key, number, rev, date):
    """The issued copy of a bridge drawing under FILES/key: dict(pdf, dxf, png, pages, bom)."""
    out = os.path.join(engines.FILES, key)
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    res = dict(pdf=os.path.join(out, "drawing.pdf"))
    res["pages"] = stamp_issue(d.files["pdf"], res["pdf"], number, rev, date)
    for kind in ("dxf", "bom"):
        if d.files.get(kind) and os.path.exists(d.files[kind]):
            res[kind] = os.path.join(out, os.path.basename(d.files[kind])); shutil.copy(d.files[kind], res[kind])
    if d.files.get("bom_name"):
        res["bom_name"] = d.files["bom_name"]
    res["png"] = os.path.join(out, "preview.png"); _png(res["pdf"], res["png"])
    return res


def production_files(r, key, title="Production drawing"):
    """The production set of an issued bridge revision: [dict(title, pdf, pages, name)], like the engine's draw_set."""
    out = os.path.join(engines.FILES, key)
    os.makedirs(out, exist_ok=True)
    pdf = os.path.join(out, "bridge-production.pdf")
    pages = stamp_production(r.files["pdf"], pdf)
    return [dict(title=title + " (BRIDGE)", pdf=pdf, pages=pages, name="%s Rev %s BRIDGE.pdf" % (r.number, r.rev))]


def open_bridges(s):
    """Products whose current drawing is a bridge drawing, on orders that are not archived: [dict(ref, no, name, request)]."""
    out = []
    for d in s.scalars(select(db.Drawing).where(db.Drawing.engine_version == BRIDGE)):
        l = s.get(db.OrderLine, d.line_id)
        o = s.get(db.Order, l.order_id) if l else None
        if o is None or o.archived_at or l.cancelled_at:
            continue
        rq = s.get(db.EngineRequest, info(d).get("request") or 0)
        out.append(dict(ref=o.source_ref, no=l.no, name=l.name.split("(")[0].strip(), request=rq.id if rq else 0,
                        request_status=rq.status if rq else "", request_text=rq.text if rq else ""))
    return out


def requests_for(s, ref, product):
    """Engine requests that can be linked to a bridge drawing of this product: this order's, newest first."""
    return list(s.scalars(select(db.EngineRequest).where(db.EngineRequest.order_ref == ref).order_by(db.EngineRequest.id.desc())))


def _line(ref, no):
    from .app import load
    o = load(ref)
    l = next((x for x in o.lines if x.no == no), None)
    if l is None:
        raise HTTPException(404)
    return o, l


def _locked(o, l):
    """A bridge drawing follows the same lock as an engine drawing: after client approval, only with Kushal's permission."""
    from .app import _panel_state, _shown
    sh = _shown(l) if (l.drawing or l.revisions) else None
    if sh is None:
        return ""
    why = _panel_state(o, l, dict(sh, engine_version=""), "client")
    return why if "locked" in why else ""


def _save(up, folder, name):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        shutil.copyfileobj(up.file, f)
    return path


@router.post("/orders/{ref}/bridge/{no}")
def upload(ref: str, no: int, pdf: UploadFile = File(None), dxf: UploadFile = File(None), bom: UploadFile = File(None),
           request_id: int = Form(0), request_text: str = Form(""), user=User):
    """Uploads a bridge drawing (a new version replaces the last; it needs the hand-check tick again)."""
    if not _may_upload(user):
        raise HTTPException(403, "Only designers, coordinators and administrators can upload a bridge drawing")
    o, l = _line(ref, no)
    if l.cancelled_at:
        raise HTTPException(400, "Product %d was cancelled" % no)
    why = _locked(o, l)
    if why:
        raise HTTPException(400, why)
    if pdf is None or not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Choose the bridge drawing as a PDF file")
    if dxf is not None and dxf.filename and not dxf.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "The DXF file must end in .dxf")
    product = l.name.split("(")[0].strip()
    with db.Session() as s:
        rq = s.get(db.EngineRequest, request_id) if request_id else None
        if rq is not None and rq.order_ref != ref:
            rq = None
        text = " ".join((request_text or "").split())
        if rq is None and not text:
            raise HTTPException(400, "A bridge drawing needs an engine request: pick this product's request or describe what the engine should learn")
        if rq is None:
            rq = db.EngineRequest(order_ref=ref[:80], product=product, text=text[:2000], asked_by=user.name)
            s.add(rq); s.flush()
        line = s.get(db.OrderLine, l.id)
        d = line.drawing or db.Drawing()
        line.drawing = d
        n = int(info(d).get("upload") or 0) + 1 if d.engine_version == BRIDGE else 1
        folder = os.path.join(engines.FILES, "%d/line%d/bridge/v%d" % (o.id, no, n))
        shutil.rmtree(folder, ignore_errors=True); os.makedirs(folder, exist_ok=True)
        files = dict(pdf=_save(pdf, folder, "drawing.pdf"))
        try:
            files["png"] = os.path.join(folder, "preview.png")
            files["pages"] = _png(files["pdf"], files["png"])
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, "That file could not be read as a PDF drawing")
        if dxf is not None and dxf.filename:
            files["dxf"] = _save(dxf, folder, "drawing.dxf")
        if bom is not None and bom.filename:
            safe = os.path.basename(bom.filename).replace("/", "_")[:120] or "bom"
            files["bom"], files["bom_name"] = _save(bom, folder, "bom-" + safe), safe
        d.engine_version, d.status, d.error, d.made_by, d.made_at = BRIDGE, "preview", "", user.name, db.now()
        d.files = files
        d.spec = dict(bridge=dict(upload=n, request=rq.id, by=user.name, at=db.now().isoformat(), checked_by="", checked_at="",
                                  files=sorted(k for k in files if k in ("pdf", "dxf", "bom"))),
                      item=dict(product_name=product, qty=int(l.qty or 0)))
        s.add(db.Message(order_id=o.id, who="desk", text="Bridge drawing v%d uploaded for Product %d by %s (made outside the engine; engine request #%d). "
                         "An administrator must tick '%s' before it can be issued." % (n, no, user.name, rq.id, CHECK)))
        s.commit()
    return RedirectResponse("/orders/%s/work#bridge%d" % (ref, no), status_code=303)


@router.post("/orders/{ref}/bridge/{no}/check")
def hand_check(ref: str, no: int, user=User):
    """The administrator's tick: the bridge drawing was checked by hand, since the engine checks cannot run on it."""
    if not _is_admin(user):
        raise HTTPException(403, "Only an administrator can tick the hand check")
    o, l = _line(ref, no)
    if not is_bridge(l):
        raise HTTPException(400, "Product %d has no bridge drawing" % no)
    with db.Session() as s:
        d = s.get(db.OrderLine, l.id).drawing
        b = info(d)
        if b.get("checked_by"):
            raise HTTPException(400, "Already checked by %s" % b["checked_by"])
        b.update(checked_by=user.name, checked_at=db.now().isoformat())
        d.spec = dict(d.spec, bridge=b)
        s.add(db.Message(order_id=o.id, who="desk", text="Bridge drawing v%s of Product %d: '%s' ticked by %s." % (b.get("upload"), no, CHECK, user.name)))
        s.commit()
    return RedirectResponse("/orders/%s/work#bridge%d" % (ref, no), status_code=303)


@router.post("/orders/{ref}/bridge/{no}/redraw")
def redraw(ref: str, no: int, user=User):
    """Draws the product on the engine again. If the engine can draw it now, its drawing becomes the preview (issued as the
    next revision) and BRIDGE is cleared; if not, the bridge drawing stays and the reason is shown."""
    if not _may_upload(user):
        raise HTTPException(403, "Only designers, coordinators and administrators can redraw a product")
    from . import spec as spec_mod
    from .app import make_spec
    o, l = _line(ref, no)
    if not is_bridge(l):
        raise HTTPException(400, "Product %d has no bridge drawing" % no)
    open_q = sum(1 for q in l.questions if not q.answer)
    if open_q:
        raise HTTPException(400, "Answer the questions of Product %d on Specify first" % no)
    edir, ver = engines.dir_for(None)                                     # the newest approved engine version
    with db.Session() as s:
        try:
            sp = make_spec(s, o, l, edir=edir)
            files = engines.draw(sp, "%d/line%d/preview" % (o.id, no), edir)
        except (spec_mod.CannotDraw, RuntimeError) as e:
            s.add(db.Message(order_id=o.id, who="desk", text="Product %d: the engine (%s) still cannot draw it, so the bridge drawing stays. %s" % (no, ver, str(e)[:300])))
            s.commit()
            return RedirectResponse("/orders/%s/work#bridge%d" % (ref, no), status_code=303)
        d = s.get(db.OrderLine, l.id).drawing
        d.engine_version, d.status, d.error, d.made_by, d.made_at, d.spec, d.files = ver, "preview", "", user.name, db.now(), sp, files
        if l.last_rev is not None:                                        # issued products stay on their version unless moved on purpose
            s.add(db.Change(line_id=l.id, typed="Redraw on the engine", understood="drawn on the engine %s instead of the bridge drawing" % ver,
                            values={"use_engine": ver}, status="confirmed", by=user.name))
        s.add(db.Message(order_id=o.id, who="desk", text="Product %d redrawn on the engine (%s) by %s: BRIDGE cleared.%s" % (
            no, ver, user.name, " Issue it as the next revision; send it to the client for approval if it differs from the approved drawing." if l.last_rev else "")))
        s.commit()
    return RedirectResponse("/orders/%s/view/%d" % (ref, no), status_code=303)


@router.get("/bridge/drawings/{did}/bom")
def bridge_bom(did: int, user=User):
    """The BOM / cut list uploaded with a bridge drawing."""
    with db.Session() as s:
        d = s.get(db.Drawing, did)
        path, name = (d.files.get("bom"), d.files.get("bom_name")) if d else (None, None)
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=name or os.path.basename(path))


@router.get("/bridge/revisions/{rid}/bom")
def bridge_rev_bom(rid: int, user=User):
    with db.Session() as s:
        r = s.get(db.Revision, rid)
        path, name = (r.files.get("bom"), r.files.get("bom_name")) if r else (None, None)
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=name or os.path.basename(path))
