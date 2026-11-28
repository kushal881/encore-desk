"""Runs the approved engines. The desk never edits them: it points at a read-only copy of an approved version (ENGINES_DIR)
and runs it as a separate program. The engine itself refuses to run if any of its files is not the approved one."""
import os, re, json, glob, subprocess, sys, tempfile, shutil

FILES = os.environ.get("FILES_DIR", "var/files")
REPO = os.environ.get("ENGINES_REPO", "kushal881/encore-approved")       # the approved engines; this program can only read it
KEY = os.environ.get("ENGINES_KEY", "")                                    # a READ-ONLY key for that repository
HOME = os.environ.get("ENGINES_HOME", "var/engines")
ENGINES_DIR = os.environ.get("ENGINES_DIR", "")                            # set by hand only when building on a developer machine
VERSION = ""


NEWER = ""                                                                 # a newer approved version found by the hourly check
CHECK_MINUTES = int(os.environ.get("ENGINES_CHECK_MINUTES", "60") or 60)


def latest_tag():
    """The newest approved version label in the approved repository (read-only). Raises when it cannot be read."""
    import httpx
    if not KEY:
        raise RuntimeError("ENGINES_KEY is not set")
    h = {"Authorization": "Bearer " + KEY, "Accept": "application/vnd.github+json"}
    r = httpx.get("https://api.github.com/repos/%s/tags?per_page=100" % REPO, headers=h, timeout=30)
    r.raise_for_status()
    nums = sorted(int(t["name"][1:]) for t in r.json() if t["name"][:1] == "v" and t["name"][1:].isdigit())
    if not nums:
        raise RuntimeError("The approved repository has no numbered version yet")
    return "v%d" % nums[-1]


def fetch_latest():
    """Downloads the newest approved version (the highest v-number) if it is not here yet, and switches to it.
    -> the version label. Older versions stay on disk so a drawing can later be rebuilt with the version it was approved on."""
    global ENGINES_DIR, VERSION, NEWER
    v = latest_tag()
    dest = _download(v)
    ENGINES_DIR, VERSION, NEWER = dest, v, ""
    return v


def _num(v):
    m = re.fullmatch(r"v(\d+)", v or "")
    return int(m.group(1)) if m else None


def check_newer():
    """Looks (read-only) for an approved version newer than the one in use. Sets and returns NEWER ("" = none).
    When the repository cannot be read, nothing is shown: NEWER is cleared and the reason only logged."""
    global NEWER
    try:
        v = latest_tag()
    except Exception as e:                                                 # noqa: BLE001
        print("Engines: could not check for a newer approved version (%s)" % str(e)[:120], flush=True)
        NEWER = ""
        return NEWER
    cur, new = _num(VERSION), _num(v)
    NEWER = v if cur is not None and new is not None and new > cur else ""
    return NEWER


def _keep_checking():
    import time
    while True:
        time.sleep(max(1, CHECK_MINUTES) * 60)
        check_newer()


def start_checking():
    """Checks for a newer approved version every CHECK_MINUTES (default 60), in the background. Not with a developer copy."""
    if ENGINES_DIR and VERSION == "developer copy":
        return None
    import threading
    th = threading.Thread(target=_keep_checking, name="engines-check", daemon=True)
    th.start()
    return th


def ensure_fonts():
    """The engines letter their sheets in DejaVu Sans. A bare server has no fonts at all, and every text then comes out as an
    empty box. The plotting package carries DejaVu Sans, so copy it to this user's font folder and rebuild the font index."""
    try:
        import matplotlib
        from ezdxf.fonts import fonts
        src = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
        dst = os.path.expanduser("~/.fonts")
        os.makedirs(dst, exist_ok=True)
        new = False
        for f in os.listdir(src):
            if f.startswith("DejaVuSans") and not os.path.exists(os.path.join(dst, f)):
                shutil.copy(os.path.join(src, f), dst); new = True
        if new or "dejavusans.ttf" not in [os.path.basename(str(x)).lower() for x in _known_fonts(fonts)]:
            fonts.build_system_font_cache()
        return "DejaVuSans.ttf" in fonts.font_manager._font_cache
    except Exception:
        return False


def _known_fonts(fonts):
    fonts.load()
    return [e.file_path for e in fonts.font_manager._font_cache._cache.values()]


def _download(v):
    """Makes sure approved version v is on disk. -> its folder. Versions are kept, never overwritten."""
    import httpx, tarfile, io
    dest = os.path.join(HOME, v)
    if os.path.exists(os.path.join(dest, "tool", "encore_drawing.py")):
        return dest
    if not KEY:
        raise RuntimeError("ENGINES_KEY is not set")
    h = {"Authorization": "Bearer " + KEY, "Accept": "application/vnd.github+json"}
    t = httpx.get("https://api.github.com/repos/%s/tarball/%s" % (REPO, v), headers=h, timeout=120, follow_redirects=True)
    t.raise_for_status()
    os.makedirs(HOME, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="eng_", dir=HOME)
    with tarfile.open(fileobj=io.BytesIO(t.content), mode="r:gz") as tf:
        tf.extractall(tmp, filter="data")
    top = os.path.join(tmp, os.listdir(tmp)[0])                     # GitHub wraps everything in one folder
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(top, dest); shutil.rmtree(tmp, ignore_errors=True)
    return dest


def dir_for(version):
    """The engines folder for an approved version label (v2, v3 ...). Anything else, or a version that cannot be had,
    means the version in use now. -> (folder, version label actually used)."""
    if version and version != VERSION and re.fullmatch(r"v\d+", version) and not os.environ.get("ENGINES_DIR"):
        try:
            return _download(version), version
        except Exception:
            pass
    return ENGINES_DIR, version_label()


def start():
    """Called when the desk starts: use the developer's copy if one is given, otherwise fetch the newest approved version."""
    global VERSION
    ensure_fonts()
    if ENGINES_DIR:
        VERSION = "developer copy"
        return
    try:
        fetch_latest()
    except Exception as e:
        VERSION = "not available (%s)" % str(e)[:80]
    start_checking()


def version_label():
    """A label for the approved version in use."""
    if VERSION.startswith("v") and VERSION[1:].isdigit():
        return VERSION
    try:
        h = json.load(open(os.path.join(ENGINES_DIR, "tool", "ENGINE_LOCK.json"), encoding="utf-8"))["history"]
        return "approval %d (%s)" % (len(h), h[-1]["date"])
    except Exception:
        return VERSION or "not available"


version = version_label


CHECKS = [("clash", "No clashes between parts"), ("true_view", "Isometric is a true view"), ("engine", "Engine check: approved engines")]


class EngineFailed(RuntimeError):
    """The engine refused to make a drawing. .checks says which automatic check stopped it."""
    def __init__(self, msg, checks):
        super().__init__(msg)
        self.checks = checks


def checks_from(output, made):
    """The three automatic checks, read from what the engine printed. made = the engine produced a drawing.
    The engine refuses to finish a drawing that clashes, whose isometric is not a true view, or whose engine files are
    not the approved ones; so a finished drawing has passed all three. -> [dict(key, title, ok, note)]"""
    low = output.lower()
    fail = dict(clash="CLASH" in output, true_view="iso true view: fail" in low, engine="engine file not approved" in low)
    note = dict(clash=next((l.strip() for l in output.splitlines() if "CLASH" in l), "").split("CLASH: ")[-1],
                true_view=next((l.strip() for l in output.splitlines() if "ISO TRUE VIEW: FAIL" in l), "").split("FAIL - ")[-1],
                engine="An engine file is not the approved one. The desk will not draw with it." if fail["engine"] else "")
    out = []
    for key, title in CHECKS:
        if fail[key]:
            ok = False
        elif made and (key != "true_view" or "iso true view: pass" in low):
            ok = True
        else:
            ok = None                                                       # not reached: an earlier step stopped the engine
        out.append(dict(key=key, title=title, ok=ok, note=note[key][:300] if ok is False else ""))
    return out


def _run(spec, edir):
    """Runs the drawing engine of folder edir on a specification. -> (work folder holding its output, the checks).
    The caller removes the folder. Raises EngineFailed when the engine made no drawing."""
    if not edir or not os.path.exists(os.path.join(edir, "tool", "encore_drawing.py")):
        raise RuntimeError("The approved engines are not available on this server (%s)." % version_label())
    work = tempfile.mkdtemp(prefix="desk_")
    sp = os.path.join(work, "spec.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), indent=1)
    if os.path.exists(os.path.join(edir, "tool", "logo.png")):
        shutil.copy(os.path.join(edir, "tool", "logo.png"), work)
    env = {k: v for k, v in os.environ.items() if k != "ENCORE_ENGINE_APPROVAL_RUN"}      # the lock check always applies here
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(edir, "tool", "encore_drawing.py"), sp, "--out", work],
                       capture_output=True, text=True, timeout=300, cwd=work, env=env)
    output = r.stdout + "\n" + r.stderr
    made = r.returncode == 0 and bool(glob.glob(os.path.join(work, "*.pdf")))
    checks = checks_from(output, made)
    if not made:
        shutil.rmtree(work, ignore_errors=True)
        lines = [l for l in output.splitlines() if l.strip()]
        msg = next((l for l in reversed(lines) if "Error" in l or "CLASH" in l or "STOP" in l or "TRUE VIEW: FAIL" in l), lines[-1] if lines else "The engine gave no output.")
        raise EngineFailed(msg.split("Error: ")[-1].strip()[:400], checks)
    return work, checks


def draw(spec, key, edir=None):
    """Runs the drawing engine on a specification. -> dict(pdf, dxf, png) paths, plus pages and checks, under FILES/key, or raises RuntimeError(message).
    edir = the folder of a particular approved version (see dir_for); the version in use when not given."""
    work, checks = _run(dict(spec, part_map=True), edir or ENGINES_DIR)     # where each part sits, for the markers (engines that know it)
    try:
        out = os.path.join(FILES, key)
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
        pdfs, dxfs = glob.glob(os.path.join(work, "*.pdf")), glob.glob(os.path.join(work, "*.dxf"))
        res = dict(pdf=os.path.join(out, "drawing.pdf"))
        shutil.copy(pdfs[0], res["pdf"])
        if dxfs:
            res["dxf"] = os.path.join(out, "drawing.dxf"); shutil.copy(dxfs[0], res["dxf"])
        maps = glob.glob(os.path.join(work, "*.parts.json"))
        if maps:
            res["parts"] = os.path.join(out, "parts.json"); shutil.copy(maps[0], res["parts"])
        import pymupdf
        res["png"] = os.path.join(out, "preview.png")
        with pymupdf.open(res["pdf"]) as doc:
            doc[0].get_pixmap(dpi=110).save(res["png"])
            res["pages"] = len(doc)
        res["checks"] = checks
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def bom(specs, key, edir=None, panel_saw=False, client="Client"):
    """The approved BOM engine (tool/bom.py) for a whole order: specs = {drawing number: issued specification}.
    panel_saw: the panel-saw cutting rules (--panel-saw: 4 mm saw cut, cut sizes, grain, optimised boards).
    -> path of the workbook under FILES/key, or raises RuntimeError with the engine's reason."""
    edir = edir or ENGINES_DIR
    tool = os.path.join(edir or "", "tool")
    if not os.path.exists(os.path.join(tool, "bom.py")):
        raise RuntimeError("The approved engines are not available on this server (%s)." % version_label())
    work = tempfile.mkdtemp(prefix="desk_bom_")
    try:
        os.makedirs(os.path.join(work, "Other"))
        safe = re.sub(r'[<>:"/\\|?*]', "", client).strip() or "Client"
        serials = []
        for number, sp in specs.items():
            json.dump(sp, open(os.path.join(work, "Other", "%s-%s.json" % (number, safe)), "w", encoding="utf-8"), indent=1)
            serials.append(str(int(number.rsplit("-", 1)[1])))
        out = os.path.join(FILES, key)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("ENCORE_ENGINE_APPROVAL_RUN", "ENCORE_PANEL_SAW")}   # the lock check always applies
        cmd = [sys.executable, "-W", "ignore", os.path.join(tool, "bom.py")] + sorted(serials, key=int) + ["--client=" + work, "--out=" + out]
        r = subprocess.run(cmd + (["--panel-saw"] if panel_saw else []), capture_output=True, text=True, timeout=900, cwd=tool, env=env)
        if r.returncode != 0 or not os.path.exists(out):
            lines = [l for l in (r.stderr + "\n" + r.stdout).splitlines() if l.strip()]
            msg = next((l for l in reversed(lines) if "Error" in l or "STOP" in l or "serial" in l), lines[-1] if lines else "The engine gave no output.")
            raise RuntimeError(msg.split("Error: ")[-1].strip()[:400])
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def has_cutting(edir=None):
    """True when the approved engines have the cutting-diagram and panel-label command (tool/board_cutting.py make)."""
    f = os.path.join(edir or ENGINES_DIR or "", "tool", "board_cutting.py")
    try:
        return "def make(" in open(f, encoding="utf-8").read()
    except OSError:
        return False


def cutting(specs, key, edir=None, client="Client"):
    """The approved engines' cutting command (tool/board_cutting.py) for a whole order: board cutting diagrams (fence plan,
    one page per board, numbered saw cuts) and 60 x 40 panel labels, from the same cut list as the bill of material with
    the panel-saw rules. specs = {drawing number: issued specification}.
    -> dict(diagrams=path, labels=path, labels_count=n) under FILES/key, or raises RuntimeError with the reason."""
    edir = edir or ENGINES_DIR
    tool = os.path.join(edir or "", "tool")
    if not has_cutting(edir):
        raise RuntimeError("The approved engines (%s) have no cutting-diagram command yet. Approve the cutting diagrams + panel labels "
                           "proposal in encore-approved, then update the engines here." % version_label())
    work = tempfile.mkdtemp(prefix="desk_cut_")
    try:
        os.makedirs(os.path.join(work, "Other"))
        safe = re.sub(r'[<>:"/\\|?*]', "", client).strip() or "Client"
        serials = []
        for number, sp in specs.items():
            json.dump(sp, open(os.path.join(work, "Other", "%s-%s.json" % (number, safe)), "w", encoding="utf-8"), indent=1)
            serials.append(str(int(number.rsplit("-", 1)[1])))
        out = os.path.join(FILES, key)
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("ENCORE_ENGINE_APPROVAL_RUN", "ENCORE_PANEL_SAW")}   # the lock check always applies
        cmd = [sys.executable, "-W", "ignore", os.path.join(tool, "board_cutting.py")] + sorted(serials, key=int) + ["--client=" + work, "--out=" + out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=tool, env=env)
        pdfs = sorted(glob.glob(os.path.join(out, "*.pdf")))
        dia = next((p for p in pdfs if "cutting diagrams" in os.path.basename(p).lower()), None)
        lab = next((p for p in pdfs if "labels" in os.path.basename(p).lower()), None)
        if r.returncode != 0 or not (dia and lab):
            if "nothing to cut" in r.stdout.lower():
                raise RuntimeError("These products have no board panels: nothing to cut on the panel saw.")
            lines = [l for l in (r.stderr + "\n" + r.stdout).splitlines() if l.strip()]
            msg = next((l for l in reversed(lines) if "Error" in l or "STOP" in l or "serial" in l), lines[-1] if lines else "The engine gave no output.")
            raise RuntimeError(msg.split("Error: ")[-1].strip()[:400])
        m = re.search(r"(\d+) labels", r.stdout)
        return dict(diagrams=dia, labels=lab, labels_count=int(m.group(1)) if m else 0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def has_board_labels(edir=None):
    """True when the approved engines can make board labels (issue #61 / engines #38: board_cutting.py --board-labels)."""
    f = os.path.join(edir or ENGINES_DIR or "", "tool", "board_cutting.py")
    try:
        return "def board_labels(" in open(f, encoding="utf-8").read()
    except OSError:
        return False


def board_labels(specs, key, edir=None, client="Client", job="", deadline="", replaces=""):
    """The approved engines' board labels for a whole order: one label per full board, in cutting sequence, from the same
    panel-saw board plan as the cutting diagrams (tool/board_cutting.py --board-labels). No client details go on a label;
    replaces = the earlier set they replace ("P1"), printed on them. specs = {drawing number: issued specification}.
    -> dict(pdf=path, boards=n, by_material=[(material, n)]) under FILES/key, or raises RuntimeError with the reason.
    The command prints "BOARDS <n>" and one "MATERIAL<TAB><name><TAB><n>" line per material."""
    edir = edir or ENGINES_DIR
    tool = os.path.join(edir or "", "tool")
    if not has_board_labels(edir):
        raise RuntimeError("The approved engines (%s) have no board labels yet. Approve the board labels proposal in encore-approved, "
                           "then update the engines here." % version_label())
    work = tempfile.mkdtemp(prefix="desk_blab_")
    try:
        os.makedirs(os.path.join(work, "Other"))
        safe = re.sub(r'[<>:"/\\|?*]', "", client).strip() or "Client"
        serials = []
        for number, sp in specs.items():
            json.dump(sp, open(os.path.join(work, "Other", "%s-%s.json" % (number, safe)), "w", encoding="utf-8"), indent=1)
            serials.append(str(int(number.rsplit("-", 1)[1])))
        out = os.path.join(FILES, key)
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("ENCORE_ENGINE_APPROVAL_RUN", "ENCORE_PANEL_SAW")}
        cmd = [sys.executable, "-W", "ignore", os.path.join(tool, "board_cutting.py")] + sorted(serials, key=int) + [
            "--client=" + work, "--out=" + out, "--board-labels", "--job=" + (job or "TBC"), "--deadline=" + (deadline or "TBC")] + (["--replaces=" + replaces] if replaces else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=tool, env=env)
        pdf = next((p for p in sorted(glob.glob(os.path.join(out, "*.pdf"))) if "board labels" in os.path.basename(p).lower()), None)
        if r.returncode != 0 or not pdf:
            if "nothing to cut" in r.stdout.lower():
                raise RuntimeError("These products have no board panels: no boards to label.")
            lines = [l for l in (r.stderr + "\n" + r.stdout).splitlines() if l.strip()]
            msg = next((l for l in reversed(lines) if "Error" in l or "STOP" in l or "serial" in l), lines[-1] if lines else "The engine gave no output.")
            raise RuntimeError(msg.split("Error: ")[-1].strip()[:400])
        m = re.search(r"^BOARDS (\d+)", r.stdout, re.M)
        mats = [(a, int(b)) for a, b in re.findall(r"^MATERIAL\t([^\t]+)\t(\d+)", r.stdout, re.M)]
        return dict(pdf=pdf, boards=int(m.group(1)) if m else sum(n for _, n in mats), by_material=mats)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def draw_set(spec, key, edir=None):
    """A production or vendor set: the engine writes several PDFs (the complete product and one set per department).
    -> [dict(title, pdf, pages)] under FILES/key, the complete-product sheet first."""
    work, _ = _run(spec, edir or ENGINES_DIR)
    try:
        import pymupdf
        out = os.path.join(FILES, key)
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
        pdfs = sorted(glob.glob(os.path.join(work, "*.pdf")))
        sets = [p for p in pdfs if p.endswith("-SET.pdf")]
        singles = [p for p in pdfs if not p.endswith("-SET.pdf")]
        dept = lambda p: (re.search(r"-(?:Production|Vendor)-(.+?)-Rev\w+(?:-SET)?\.pdf$", os.path.basename(p)) or [None, ""])[1]
        keep = [p for p in singles if not dept(p)] + sets + [p for p in singles if dept(p) and dept(p) not in [dept(x) for x in sets]]
        res = []
        for i, p in enumerate(keep):
            dst = os.path.join(out, "%02d.pdf" % i)
            shutil.copy(p, dst)
            with pymupdf.open(dst) as d:
                n = len(d)
            res.append(dict(title=dept(p) or "Complete product", pdf=dst, pages=n, name=os.path.basename(p)))
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
