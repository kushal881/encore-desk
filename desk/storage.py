"""Kept files: the private storage bucket (brief: Storage, build step 7).

The engine still writes its files to the desk's disk (FILES_DIR) while it works; that copy is only a working copy.
Every file that is kept (issued drawings PDF / DXF, a light preview picture per sheet, production drawings, the bill of
material, approval proofs) is also put in one private, S3-compatible Railway Storage Bucket. Nobody gets a bucket address
that lasts: the desk or the portal checks who is asking, then hands out a signed link that works for a few minutes.
Every view and download is logged (db.FileLog).

Layout in the bucket, one folder per client:
    Encore-<Mon>-<YEAR>-<folder no>-<Company>/PDF/            issued client drawings, <number>-Rev<X>.pdf
                                              DXF/
                                              Old Revisions/  earlier revisions, moved here when a new one is issued
                                              Bill of Material/
                                              Other/          production drawings, approval proofs
                                              _previews/      one picture per sheet, for quick viewing

Set in Railway (the bucket's own variables, by reference): BUCKET, ACCESS_KEY_ID, SECRET_ACCESS_KEY, ENDPOINT, REGION.
The AWS_* names that S3 libraries use (AWS_S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL,
AWS_DEFAULT_REGION) work too. SIGNED_LINK_MINUTES (default 5). When no bucket is set (building on a laptop), files are
served from the disk as before, still logged."""
import hashlib, os, re, threading
from urllib.parse import quote
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from . import db

_CLIENT = None                          # the S3 client; tests put a stand-in here
_LOCK = threading.Lock()


def _env(*names, default=""):
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return default


def bucket():
    return _env("BUCKET", "AWS_S3_BUCKET_NAME", "S3_BUCKET")


def configured():
    return bool(bucket() and (_CLIENT is not None or (_env("ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID") and _env("SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"))))


def minutes():
    try:
        return max(1, min(60, int(os.environ.get("SIGNED_LINK_MINUTES", "5"))))
    except ValueError:
        return 5


def client():
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            import boto3
            from botocore.config import Config
            _CLIENT = boto3.client("s3", endpoint_url=_env("ENDPOINT", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3") or None,
                                   region_name=_env("REGION", "AWS_DEFAULT_REGION", "AWS_REGION", default="auto"),
                                   aws_access_key_id=_env("ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
                                   aws_secret_access_key=_env("SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
                                   config=Config(signature_version="s3v4", s3={"addressing_style": os.environ.get("BUCKET_ADDRESSING", "virtual")}))
        return _CLIENT


# ---- names in the bucket ---------------------------------------------------------------------------------------------

def safe(text):
    """A name part that is safe in a bucket key and on a Windows share."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w .&()-]", "", text or "")).strip() or "Client"


def client_folder(first_number, company):
    """Encore-Nov-2026-06-02 + Achievers Apparels -> Encore-Nov-2026-06-Achievers Apparels (the client's first drawing
    number without its serial). No number yet (nothing issued) -> None."""
    if not first_number or first_number.count("-") < 4:
        return None
    return "%s-%s" % (first_number.rsplit("-", 1)[0], safe(company))


def revision_keys(folder, number, rev, pages):
    """Bucket names of one issued revision: pdf, dxf, and one preview picture per sheet."""
    base = "%s-Rev%s" % (number, rev)
    return dict(pdf="%s/PDF/%s.pdf" % (folder, base), dxf="%s/DXF/%s.dxf" % (folder, base),
                previews=["%s/_previews/%s-%d.png" % (folder, base, n + 1) for n in range(max(1, pages))])


def old_key(key):
    """Where an earlier revision goes when a new one is issued: PDF/x -> Old Revisions/x (DXF too)."""
    folder, kind, name = key.split("/", 2)
    return "%s/Old Revisions/%s" % (folder, name) if kind in ("PDF", "DXF") else key


# ---- keeping and finding files ---------------------------------------------------------------------------------------

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


CONTENT = {".pdf": "application/pdf", ".dxf": "application/dxf", ".png": "image/png",
           ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def keep(path, key):
    """Puts one file in the bucket under key and remembers it. -> the key, or "" when there is no bucket, no such file, or
    the upload failed (then the disk copy stays the one served, and "Copy files to the bucket" tries again later)."""
    if not configured() or not path or not os.path.exists(path):
        return ""
    with db.Session() as s:
        sf = s.scalar(select(db.StoredFile).where(db.StoredFile.path == path))
        if sf and sf.key == key:
            return key
    try:
        ct = CONTENT.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        client().upload_file(Filename=path, Bucket=bucket(), Key=key, ExtraArgs={"ContentType": ct})
    except Exception as e:                                             # noqa: BLE001 -- never lose the issue over the copy
        print("Storage: could not keep %s (%s)" % (key, type(e).__name__), flush=True)
        return ""
    with db.Session() as s:
        sf = s.scalar(select(db.StoredFile).where(db.StoredFile.path == path)) or db.StoredFile(path=path)
        sf.key, sf.size, sf.sha256 = key, os.path.getsize(path), _sha(path)
        s.add(sf)
        s.commit()
    return key


def move(path, new_key):
    """Moves a kept file to another name in the bucket (an earlier revision to Old Revisions/)."""
    with db.Session() as s:
        sf = s.scalar(select(db.StoredFile).where(db.StoredFile.path == path))
        if not configured() or sf is None or sf.key == new_key:
            return
        try:
            c = client()
            c.copy_object(Bucket=bucket(), Key=new_key, CopySource={"Bucket": bucket(), "Key": sf.key})
            c.delete_object(Bucket=bucket(), Key=sf.key)
        except Exception as e:                                         # noqa: BLE001
            print("Storage: could not move %s (%s)" % (sf.key, type(e).__name__), flush=True)
            return
        sf.key = new_key
        s.commit()


def key_of(path):
    if not path:
        return ""
    with db.Session() as s:
        return s.scalar(select(db.StoredFile.key).where(db.StoredFile.path == path)) or ""


def local(path):
    """A file the desk needs to read itself (to join the set, take a fingerprint, show a sheet): the disk copy, fetched
    back from the bucket when the disk no longer has it. -> path, or None when it is nowhere."""
    if path and os.path.exists(path):
        return path
    k = key_of(path)
    if not k or not configured():
        return None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        client().download_file(Bucket=bucket(), Key=k, Filename=path)
        return path
    except Exception:                                                  # noqa: BLE001
        return None


def signed(key, name, download=False):
    """A link to one kept file that works for a few minutes only."""
    disp = "%s; filename*=UTF-8''%s" % ("attachment" if download else "inline", quote(name or os.path.basename(key)))
    return client().generate_presigned_url("get_object", Params={"Bucket": bucket(), "Key": key, "ResponseContentDisposition": disp},
                                           ExpiresIn=minutes() * 60)


def log(who, side, action, name, key="", order_id=0, ip=""):
    with db.Session() as s:
        s.add(db.FileLog(who=(who or "")[:200], side=side, action=action, name=name or "", key=key or "", order_id=order_id or 0, ip=(ip or "")[:60]))
        s.commit()


def serve(request, path, name=None, download=False, who="", side="desk", order_id=0, inline_name=True):
    """Hands one file to someone the caller has already checked: a signed link to the bucket copy when there is one,
    otherwise the disk copy. Logged either way. 404 when the file is nowhere."""
    ip = request.client.host if request is not None and request.client else ""
    k = key_of(path) if configured() else ""
    shown = name or os.path.basename(path or "")
    if k:
        log(who, side, "download" if download else "view", shown, k, order_id, ip)
        return RedirectResponse(signed(k, shown, download), status_code=302)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "This file is not there any more")
    log(who, side, "download" if download else "view", shown, "", order_id, ip)
    return FileResponse(path, filename=shown if (download or inline_name) else None, content_disposition_type="attachment" if download else "inline")


# ---- previews -------------------------------------------------------------------------------------------------------

def previews(pdf_path, dpi=110):
    """A light picture of every sheet of a PDF, kept beside it (<pdf>.sheet<n>.png). -> [paths], one per sheet."""
    import pymupdf
    out = []
    with pymupdf.open(pdf_path) as doc:
        for n, page in enumerate(doc):
            p = "%s.sheet%d.png" % (pdf_path[:-4], n + 1)
            if not os.path.exists(p) or os.path.getmtime(p) < os.path.getmtime(pdf_path):
                page.get_pixmap(dpi=dpi).save(p)
            out.append(p)
    return out


def preview_paths(pdf_path):
    """The preview pictures of an issued PDF, made if they are not there yet (old revisions made before previews)."""
    if not pdf_path:
        return []
    src = local(pdf_path)
    if src:
        return previews(src)
    # the PDF is in the bucket only and could not be fetched: use the previews the bucket has
    with db.Session() as s:
        stem = pdf_path[:-4] + ".sheet"
        return list(s.scalars(select(db.StoredFile.path).where(db.StoredFile.path.like(stem + "%")).order_by(db.StoredFile.path)))


def keep_revision(folder, number, rev, files, earlier=()):
    """Keeps one issued revision in the bucket: PDF, DXF and a preview per sheet. The client's earlier revisions of the
    same drawing (their files dicts) move to Old Revisions/. -> number of files kept."""
    if not configured() or not folder or not files.get("pdf") or not os.path.exists(files["pdf"]):
        return 0
    pv = previews(files["pdf"])
    keys = revision_keys(folder, number, rev, len(pv))
    done = bool(keep(files["pdf"], keys["pdf"]))
    if files.get("dxf"):
        done += bool(keep(files["dxf"], keys["dxf"]))
    for p, k in zip(pv, keys["previews"]):
        done += bool(keep(p, k))
    for old in earlier:
        for kind in ("pdf", "dxf"):
            k = key_of(old.get(kind))
            if k:
                move(old.get(kind), old_key(k))
    return done
