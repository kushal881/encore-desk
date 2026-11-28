"""Client references (issue #72): sketches, photos, site measurements, PDFs and CAD files the client gave us, kept with
the project so the designer answers questions and checks the drawing against them.

- Staff (designers, coordinators, salespeople, administrators) upload them on the project page, on Specify and in the
  viewer: jpg / png / heic photos, PDF, DXF / DWG, 25 MB at most, with a short note, for the whole order or one product.
- Shown as thumbnails on Specify, in a side panel of the viewer and on the project page; a click opens the full file
  through the desk (a signed bucket link when kept there), logged like every other file.
- Kept in the private bucket under the client's folder, References/ (once the client's folder exists: the first issue).
- Never on the client portal unless staff tick "show to client"; production sees only those ticked "for production".
- A file the client attaches to a change request on the portal lands in the same list (shown to the client: they sent it).
- The AI assistant may read a product's reference pictures when staff tick it in the viewer, to propose values for the
  product file; it still never draws (Accept / Undo as usual). Off without the API key.
Routes are kept here, apart from app.py; app.py includes this router at its end."""
import base64, io, os, re
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from . import auth, db, engines, storage
from .app import T, User

router = APIRouter()
MAX_MB = 25
KINDS = {".jpg": "image", ".jpeg": "image", ".png": "image", ".heic": "photo", ".heif": "photo", ".pdf": "pdf", ".dxf": "cad", ".dwg": "cad"}
UPLOADERS = ("admin", "admin2", "drawing", "coordinator", "sales")


def kind_of(name):
    return KINDS.get(os.path.splitext(name or "")[1].lower(), "")


def may_upload(user):
    return user.role in UPLOADERS or auth.level(user) in ("admin", "admin2")


def may_see(user, r):
    """Every staff member except production, who sees only references ticked for production."""
    return r is not None and (user.role != "production" or r.for_production)


def listed(order_id, line_no=None, production=False, client=False):
    """The references of an order, oldest first: line_no None = all; a number = that product's and the whole order's."""
    with db.Session() as s:
        q = select(db.Reference).where(db.Reference.order_id == order_id).order_by(db.Reference.id)
        rows = list(s.scalars(q))
    if line_no is not None:
        rows = [r for r in rows if r.line_no in (0, line_no)]
    if production:
        rows = [r for r in rows if r.for_production]
    if client:
        rows = [r for r in rows if r.show_client]
    return rows


def for_user(order_id, line_no, role):
    """For the templates: what someone with this role (the label the pages show) may see of an order's references."""
    return listed(order_id, line_no, production=role in ("production", auth.ROLES["production"]))


def _thumb(path, kind):
    """A small picture of the file for the lists (photos and PDFs). -> path or ""."""
    out = path + ".thumb.png"
    try:
        if kind == "image":
            from PIL import Image
            im = Image.open(path); im = im.convert("RGB"); im.thumbnail((360, 360)); im.save(out)
        elif kind == "pdf":
            import pymupdf
            with pymupdf.open(path) as doc:
                pix = doc[0].get_pixmap(dpi=40)
                pix.save(out)
        else:
            return ""
    except Exception:                                                     # noqa: BLE001 -- a file we cannot picture still uploads
        return ""
    return out


def _keep(r, client_id):
    """Into the bucket under the client's folder, References/ (nothing until the folder exists)."""
    from .app import _folder
    with db.Session() as s:
        folder = _folder(s, client_id)
    if folder and r.path:
        storage.keep(r.path, "%s/References/%d-%s" % (folder, r.id, storage.safe(r.name)))


def keep_all(order_id):
    """Called after an issue: references uploaded before the client's folder existed go into it now."""
    with db.Session() as s:
        o = s.get(db.Order, order_id)
        rows = list(s.scalars(select(db.Reference).where(db.Reference.order_id == order_id)))
        cid = o.client_id if o else 0
    for r in rows:
        if not storage.key_of(r.path):
            _keep(r, cid)


def add(order, line_no, name, data, note, by, source="desk", show_client=False, for_production=False):
    """Saves one reference file. -> the Reference, or raises HTTPException with the reason in plain words."""
    kind = kind_of(name)
    if not kind:
        raise HTTPException(400, "This file type cannot be kept as a reference. Use a photo (jpg, png, heic), a PDF, or a DXF / DWG drawing.")
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(400, "The file is larger than %d MB" % MAX_MB)
    safe = re.sub(r"[^\w.\-]+", "_", os.path.basename(name))[-80:]
    with db.Session() as s:
        r = db.Reference(order_id=order.id, line_no=line_no, name=os.path.basename(name)[:200], kind=kind, note=" ".join((note or "").split())[:500],
                         by=by[:120], source=source, show_client=show_client, for_production=for_production)
        s.add(r); s.flush()
        r.path = os.path.join(engines.FILES, str(order.id), "references", "%d-%s" % (r.id, safe))
        os.makedirs(os.path.dirname(r.path), exist_ok=True)
        with open(r.path, "wb") as f:
            f.write(data)
        r.thumb = _thumb(r.path, kind)
        what = "Product %d" % line_no if line_no else "the whole order"
        s.add(db.Message(order_id=order.id, who="desk", text="Client reference added for %s by %s: %s%s." % (what, by, r.name, " (%s)" % r.note if r.note else "")))
        s.commit()
        s.refresh(r)
        s.expunge(r)
    _keep(r, order.client_id)
    return r


def _order(ref):
    with db.Session() as s:
        o = s.scalar(select(db.Order).where(db.Order.source_ref == ref))
        if o is None:
            raise HTTPException(404)
        s.expunge(o)
    return o


def _back(back, ref):
    return back if (back or "").startswith("/orders/%s" % ref) else "/orders/%s#references" % ref


@router.post("/orders/{ref}/references")
async def upload(ref: str, file: UploadFile = File(None), note: str = Form(""), line_no: int = Form(0), show_client: str = Form(""),
                 for_production: str = Form(""), back: str = Form(""), user=User):
    if not may_upload(user):
        raise HTTPException(403, "Production cannot add client references")
    o = _order(ref)
    with db.Session() as s:
        nos = {l.no for l in s.scalars(select(db.OrderLine).where(db.OrderLine.order_id == o.id))}
    if line_no and line_no not in nos:
        raise HTTPException(400, "There is no Product %d in this order" % line_no)
    if file is None or not file.filename:
        raise HTTPException(400, "Choose a file first")
    data = await file.read(MAX_MB * 1024 * 1024 + 1)
    add(o, line_no, file.filename, data, note, user.name, show_client=bool(show_client), for_production=bool(for_production))
    return RedirectResponse(_back(back, ref), status_code=303)


def _get(rid, user):
    with db.Session() as s:
        r = s.get(db.Reference, rid)
        if r is not None:
            s.expunge(r)
    if not may_see(user, r):
        raise HTTPException(404)
    return r


@router.get("/references/{rid}")
def open_file(request: Request, rid: int, download: int = 0, user=User):
    r = _get(rid, user)
    return storage.serve(request, r.path, r.name, download=bool(download) or r.kind in ("cad", "photo"), who=user.name, order_id=r.order_id)


@router.get("/references/{rid}/thumb")
def thumb(rid: int, user=User):
    r = _get(rid, user)
    if not r.thumb or not os.path.exists(r.thumb):
        raise HTTPException(404)
    return FileResponse(r.thumb)


@router.post("/references/{rid}/flags")
def flags(rid: int, show_client: str = Form(""), for_production: str = Form(""), back: str = Form(""), user=User):
    """Ticks who else may see it: the client on the portal, production."""
    if not may_upload(user):
        raise HTTPException(403)
    with db.Session() as s:
        r = s.get(db.Reference, rid)
        if r is None:
            raise HTTPException(404)
        o = s.get(db.Order, r.order_id)
        before = (r.show_client, r.for_production)
        r.show_client, r.for_production = bool(show_client), bool(for_production)
        if before != (r.show_client, r.for_production):
            s.add(db.Message(order_id=r.order_id, who="desk", text="Client reference %s: %s, %s (by %s)." % (
                r.name, "shown to the client" if r.show_client else "not shown to the client", "for production" if r.for_production else "not for production", user.name)))
        s.commit()
        ref = o.source_ref
    return RedirectResponse(_back(back, ref), status_code=303)


@router.post("/references/{rid}/remove")
def remove(rid: int, back: str = Form(""), user=User):
    """The person who added it, or an administrator, takes it off the list."""
    with db.Session() as s:
        r = s.get(db.Reference, rid)
        if r is None:
            raise HTTPException(404)
        if r.by != user.name and auth.level(user) not in ("admin", "admin2"):
            raise HTTPException(403, "Only the person who added it or an administrator can remove it")
        o = s.get(db.Order, r.order_id)
        s.add(db.Message(order_id=r.order_id, who="desk", text="Client reference %s removed by %s." % (r.name, user.name)))
        s.delete(r)
        s.commit()
        ref = o.source_ref
    return RedirectResponse(_back(back, ref), status_code=303)


def images_for_ai(order_id, line_no, limit=3):
    """The product's reference pictures for the assistant: [(media type, base64 data)], each at most 1568 px."""
    from PIL import Image
    out = []
    for r in reversed(listed(order_id, line_no)):
        if r.kind != "image":
            continue
        path = storage.local(r.path)
        if not path:
            continue
        try:
            im = Image.open(path); im = im.convert("RGB"); im.thumbnail((1568, 1568))
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
            out.append(("image/jpeg", base64.b64encode(buf.getvalue()).decode()))
        except Exception:                                                 # noqa: BLE001
            continue
        if len(out) >= limit:
            break
    return out


T.env.globals["references_for"] = for_user                               # used by the project, Specify, viewer and production pages
