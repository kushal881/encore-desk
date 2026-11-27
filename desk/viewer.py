"""The rules of the drawing viewer: which of the 8 steps a product is at, the automatic checks shown beside the drawing,
the one next step at the foot of the page, sheet keys for markers and the placeholders of a production preview.
No drawing is made here: the engine does all drawing."""
from . import engines

STEPS = ["Order", "Specify", "Generate", "Review", "Issue", "Client approval", "Production", "Done"]


def step_of(stage, l):
    """-> (number 1..8, name) for one product. stage = the order's stage (see app.stage_of)."""
    d = l.drawing
    if d is None or d.status == "failed":
        n = 2 if l.open_count else 3
    elif d.status == "preview" and not l.issued_current:
        n = 4                                                          # a preview that is not issued is waiting for review
    else:
        n = dict(drawing=4, issued=5, sent=6, approved=7, production=7, completed=8).get(stage, 4)
    return n, STEPS[n - 1]


def check_rows(checks):
    """The automatic checks of the drawing shown. checks = what the engine run recorded (engines.checks_from), or None
    for a drawing made before the desk recorded them. -> [dict(title, state, text, note)]; state pass | fail | wait | none."""
    if not checks:
        return [dict(title=t, state="none", text="Not recorded", note="") for _, t in engines.CHECKS]
    rows = []
    for c in checks:
        state = "pass" if c["ok"] else "fail" if c["ok"] is False else "wait"
        rows.append(dict(title=c["title"], state=state, text={"pass": "Pass", "fail": "Fail", "wait": "Not reached"}[state], note=c.get("note", "")))
    return rows


def all_pass(checks):
    return bool(checks) and all(c["ok"] for c in checks)


def next_step(l, shown_is_preview, checks, review, asked, mode):
    """The one next step at the foot of the viewer. -> dict(kind, title, text). kind:
    ready (button: mark ready to issue) | done (marked; go to issue) | redraw (button: draw again to run the checks) |
    blocked (a check failed) | confirm (a change request waits) | failed (no drawing could be made) | info."""
    if mode == "prod":
        return dict(kind="info", title="Production drawing", text="Changes to construction are made on the production screen. Switch back to the client drawing to review it.")
    if l.drawing is not None and l.drawing.status == "failed" and not shown_is_preview:
        return dict(kind="failed", title="This drawing could not be made", text="The engine stopped. See the checks. If it needs something new, ask for an engine addition in Questions and changes.")
    if not shown_is_preview:
        return dict(kind="info", title="This drawing is issued", text="An issued drawing is never changed. A change makes a new revision.")
    if asked:
        return dict(kind="confirm", title="Confirm the change request first", text="A change to this product is waiting for your yes or no in Questions and changes.")
    if not checks:
        return dict(kind="redraw", title="Run the automatic checks", text="This preview was made before the desk showed its checks. Draw it again to run them; nothing is sent to the client.")
    if not all_pass(checks):
        return dict(kind="blocked", title="This drawing cannot be issued", text="An automatic check failed. Change the product so that it passes, or ask for an engine addition.")
    if review:
        return dict(kind="done", title="Ready to issue", text="Marked by %s." % review.by)
    return dict(kind="ready", title="Looks right?", text="Check every sheet, then mark it ready to issue.")


def sheet_key(key, sheet):
    """Markers are kept per sheet: sheet 1 keeps the drawing key itself (as before), sheet 2 is <key>s2 and so on."""
    return key if sheet <= 1 else "%ss%d" % (key, sheet)


def placeholders(job_order=""):
    """The job order and dates written on a production preview until the real ones are known."""
    return dict(job_order=job_order or "JO-[NO.]", job_order_date="[DATE]", deadline="[DATE]")
