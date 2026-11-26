"""Runs the approved engines. The desk never edits them: it points at a read-only copy of an approved version (ENGINES_DIR)
and runs it as a separate program. The engine itself refuses to run if any of its files is not the approved one."""
import os, json, glob, subprocess, sys, tempfile, shutil

ENGINES_DIR = os.environ.get("ENGINES_DIR", "/home/claude/approved_engines")
FILES = os.environ.get("FILES_DIR", "var/files")


def version():
    """A label for the approved version in use: how many approvals its lock records, and the last one."""
    try:
        h = json.load(open(os.path.join(ENGINES_DIR, "tool", "ENGINE_LOCK.json"), encoding="utf-8"))["history"]
        return "approval %d (%s)" % (len(h), h[-1]["date"])
    except Exception:
        return "unknown"


def draw(spec, key):
    """Runs the drawing engine on a specification. -> dict(pdf, dxf, png) paths under FILES/key, or raises RuntimeError(message)."""
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
