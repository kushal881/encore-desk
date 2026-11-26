"""Runs the approved engines. The desk never edits them: it points at a read-only copy of an approved version (ENGINES_DIR)
and runs it as a separate program. The engine itself refuses to run if any of its files is not the approved one."""
import os, json, glob, subprocess, sys, tempfile, shutil

FILES = os.environ.get("FILES_DIR", "var/files")
REPO = os.environ.get("ENGINES_REPO", "kushal881/encore-approved")       # the approved engines; this program can only read it
KEY = os.environ.get("ENGINES_KEY", "")                                    # a READ-ONLY key for that repository
HOME = os.environ.get("ENGINES_HOME", "var/engines")
ENGINES_DIR = os.environ.get("ENGINES_DIR", "")                            # set by hand only when building on a developer machine
VERSION = ""


def fetch_latest():
    """Downloads the newest approved version (the highest v-number) if it is not here yet, and switches to it.
    -> the version label. Older versions stay on disk so a drawing can later be rebuilt with the version it was approved on."""
    global ENGINES_DIR, VERSION
    import httpx, tarfile, io
    if not KEY:
        raise RuntimeError("ENGINES_KEY is not set")
    h = {"Authorization": "Bearer " + KEY, "Accept": "application/vnd.github+json"}
    r = httpx.get("https://api.github.com/repos/%s/tags?per_page=100" % REPO, headers=h, timeout=30)
    r.raise_for_status()
    nums = sorted(int(t["name"][1:]) for t in r.json() if t["name"][:1] == "v" and t["name"][1:].isdigit())
    if not nums:
        raise RuntimeError("The approved repository has no numbered version yet")
    v = "v%d" % nums[-1]
    dest = os.path.join(HOME, v)
    if not os.path.exists(os.path.join(dest, "tool", "encore_drawing.py")):
        t = httpx.get("https://api.github.com/repos/%s/tarball/%s" % (REPO, v), headers=h, timeout=120, follow_redirects=True)
        t.raise_for_status()
        tmp = tempfile.mkdtemp(prefix="eng_", dir=os.path.dirname(os.path.abspath(dest)) if os.path.isdir(os.path.dirname(os.path.abspath(dest))) else None)
        with tarfile.open(fileobj=io.BytesIO(t.content), mode="r:gz") as tf:
            tf.extractall(tmp, filter="data")
        top = os.path.join(tmp, os.listdir(tmp)[0])                 # GitHub wraps everything in one folder
        os.makedirs(HOME, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(top, dest); shutil.rmtree(tmp, ignore_errors=True)
    ENGINES_DIR, VERSION = dest, v
    return v


def start():
    """Called when the desk starts: use the developer's copy if one is given, otherwise fetch the newest approved version."""
    global VERSION
    if ENGINES_DIR:
        VERSION = "developer copy"
        return
    try:
        fetch_latest()
    except Exception as e:
        VERSION = "not available (%s)" % str(e)[:80]


def version():
    """A label for the approved version in use."""
    if VERSION.startswith("v") and VERSION[1:].isdigit():
        return VERSION
    try:
        h = json.load(open(os.path.join(ENGINES_DIR, "tool", "ENGINE_LOCK.json"), encoding="utf-8"))["history"]
        return "approval %d (%s)" % (len(h), h[-1]["date"])
    except Exception:
        return VERSION or "not available"


def draw(spec, key):
    """Runs the drawing engine on a specification. -> dict(pdf, dxf, png) paths under FILES/key, or raises RuntimeError(message)."""
    if not ENGINES_DIR or not os.path.exists(os.path.join(ENGINES_DIR, "tool", "encore_drawing.py")):
        raise RuntimeError("The approved engines are not available on this server (%s)." % version())
    out = os.path.join(FILES, key)
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="desk_")
    try:
        sp = os.path.join(work, "spec.json")
        json.dump(spec, open(sp, "w", encoding="utf-8"), indent=1)
        shutil.copy(os.path.join(ENGINES_DIR, "tool", "logo.png"), work) if os.path.exists(os.path.join(ENGINES_DIR, "tool", "logo.png")) else None
        env = {k: v for k, v in os.environ.items() if k != "ENCORE_ENGINE_APPROVAL_RUN"}      # the lock check always applies here
        r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(ENGINES_DIR, "tool", "encore_drawing.py"), sp, "--out", work],
                           capture_output=True, text=True, timeout=300, cwd=work, env=env)
        pdfs, dxfs = glob.glob(os.path.join(work, "*.pdf")), glob.glob(os.path.join(work, "*.dxf"))
        if r.returncode != 0 or not pdfs:
            lines = [l for l in (r.stderr + "\n" + r.stdout).splitlines() if l.strip()]
            msg = next((l for l in reversed(lines) if "Error" in l or "CLASH" in l or "STOP" in l), lines[-1] if lines else "The engine gave no output.")
            raise RuntimeError(msg.split("Error: ")[-1].strip()[:400])
        res = dict(pdf=os.path.join(out, "drawing.pdf"))
        shutil.copy(pdfs[0], res["pdf"])
        if dxfs:
            res["dxf"] = os.path.join(out, "drawing.dxf"); shutil.copy(dxfs[0], res["dxf"])
        import pymupdf
        res["png"] = os.path.join(out, "preview.png")
        pymupdf.open(res["pdf"])[0].get_pixmap(dpi=110).save(res["png"])
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
