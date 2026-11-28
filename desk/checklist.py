"""Order checklist (issue #44): every step from new project to done for one order, numbered, with a status, who did it and
when (from the order's own history), the reason when it is blocked, and the button that does it (the same places as the
existing buttons; no new logic). Worked out from what the order holds, so projects made before this have a correct list.

Statuses: done (green tick), now (amber), later (grey), blocked (red, with the reason), optional (grey, never blocks).
Steps that do not apply are left out (quotation changes only when there are some; subcontractor drawings only when made)."""
from sqlalchemy import select
from . import db

NOW_STATES = ("now", "blocked")


def _step(no, title, done, note="", who="", when=None, url="", label="", block="", optional=False):
    return dict(no=no, title=title, done=done, note=note, who=who, when=when, url=url, label=label, block=block, optional=optional, state="")


PRODUCTION_STEPS = ("Production drawings made", "Subcontractor drawings made", "Bill of material and cut list made")


def steps(o, production=True):
    """-> list of steps for one loaded order (app.load), numbered in order, with their state. production=False leaves out
    the production-drawing steps (for a salesperson, who does not see production drawings)."""
    from .app import _ready, _approval_of, _locked, _client_changes
    ref, lines = o.source_ref, [l for l in o.lines if not getattr(l, "cancelled_at", None)]
    n = len(lines)
    with db.Session() as s:
        access = list(s.scalars(select(db.PortalAccess).where(db.PortalAccess.order_id == o.id).order_by(db.PortalAccess.id)))
        mails = list(s.scalars(select(db.EmailLog).where(db.EmailLog.order_id == o.id, db.EmailLog.error == "").order_by(db.EmailLog.id)))
        quotes = list(s.scalars(select(db.QuoteChange).where(db.QuoteChange.order_id == o.id)))
        boms = list(s.scalars(select(db.ProductionFile).where(db.ProductionFile.order_id == o.id, db.ProductionFile.kind == "bom", db.ProductionFile.error == "")
                              .order_by(db.ProductionFile.id)))
        started = next((m for m in o.messages if m.text.startswith("Project started by")), None)
        client_open = [l.no for l in lines if l.revisions and _client_changes(s, l.revisions[-1].id)]
    step_of = {st.kind: st for st in o.steps}                            # the latest of each kind
    out = []
    # 1-2: the project and its products
    out.append(_step(1, "Project created from the Odoo quotation", True,
                     note=("server folder %s" % o.client.folder_no) if o.client.folder_no else "server folder no. is asked at the first issue",
                     who=started.text.split("by ")[1].split(".")[0] if started else "", when=started.at if started else o.read_at))
    aside = len(o.not_drawn)
    out.append(_step(2, "Products read; bought-out and service lines set aside", True,
                     note="%d product%s to draw%s" % (n, "" if n == 1 else "s", ", %d set aside (no drawing needed)" % aside if aside else ""), when=o.read_at))
    # 3-6: specify, draw, review
    left = sum(l.open_count for l in lines)
    answered = [q for l in lines for q in l.questions if q.answered_at]
    last_q = max(answered, key=lambda q: q.answered_at.replace(tzinfo=None)) if answered else None
    out.append(_step(3, "All questions answered", left == 0, note="%d left" % left if left else "", url="/orders/%s/specify" % ref, label="Answer",
                     who=last_q.answered_by if last_q and not left else "", when=last_q.answered_at if last_q and not left else None))
    areas = sum(1 for l in lines if l.areas)
    out.append(_step(4, "Areas set (optional)", areas == n and n > 0, note="%d of %d products" % (areas, n) if areas else "", url="/orders/%s/specify" % ref,
                     label="Set areas", optional=True))
    drawn = [l for l in lines if l.revisions or (l.drawing and l.drawing.status == "preview")]
    failed = [l.no for l in lines if l.drawing and l.drawing.status == "failed" and not l.revisions]
    made = [l.drawing for l in lines if l.drawing and l.drawing.made_at]
    last_d = max(made, key=lambda d: d.made_at.replace(tzinfo=None)) if made else None
    out.append(_step(5, "All previews drawn", n > 0 and len(drawn) == n, note="%d of %d products" % (len(drawn), n),
                     block="Product %s could not be drawn" % ", ".join(map(str, failed)) if failed else "",
                     url="/orders/%s/work" % ref, label="Draw previews", who=last_d.made_by if last_d and len(drawn) == n else "",
                     when=last_d.made_at if last_d and len(drawn) == n else None))
    ai = [l.no for l in lines if getattr(l, "ai_waiting", False)]
    reviewed = [l for l in lines if l.issued_current or (_ready(l) and l.review)]
    out.append(_step(6, "All drawings reviewed: no AI change waiting, checks passed, marked ready", n > 0 and len(reviewed) == n,
                     note="%d of %d products" % (len(reviewed), n),
                     block="Product %s: Accept or Undo the change first" % ", ".join(map(str, ai)) if ai else "",
                     url="/orders/%s/view/%d" % (ref, (next((l for l in lines if l not in reviewed), lines[0]).no)) if lines else "", label="Review"))
    # 7-9: issue and the client
    out.append(_step(7, "Client added to the portal access list", bool(access), note=", ".join(a.email for a in access[:3]),
                     who=access[0].added_by if access else "", when=access[0].at if access else None, url="/orders/%s/issue" % ref, label="Add the client"))
    issued = [l for l in lines if l.last_rev and l.issued_current]
    st_issue, st_sent = step_of.get("issued"), step_of.get("sent")
    sent = bool(st_sent) or bool(mails)
    revs = sorted({l.last_rev.rev for l in issued}) if issued else []
    out.append(_step(8, "Drawings issued and sent to the client", n > 0 and len(issued) == n and sent,
                     note="%d of %d issued%s%s" % (len(issued), n, " (Rev %s)" % ", ".join(revs) if revs else "", "" if sent else "; not sent yet"),
                     who=(st_sent.by if st_sent else mails[-1].by if mails else "") if sent else "", when=(st_sent.at if st_sent else mails[-1].at if mails else None) if sent else None,
                     url="/orders/%s/issue" % ref, label="Issue and send"))
    appr = [l for l in lines if l.last_rev and (( _approval_of(l.last_rev) is not None and _approval_of(l.last_rev).outcome == "approved") or _locked(o, l))]
    whole = o.stage in ("approved", "production", "completed") and not o.approval_pending
    ok9 = n > 0 and (len(appr) == n or whole)
    last_ap = None
    if ok9 and appr:
        aps = [a for a in (_approval_of(l.last_rev) for l in appr) if a is not None]
        last_ap = max(aps, key=lambda a: a.at.replace(tzinfo=None)) if aps else None
    out.append(_step(9, "Client approval: all drawings approved", ok9, note="%d of %d approved" % (n if whole else len(appr), n) +
                     ("; changes asked on Product %s: apply, accept, re-issue (back to step 8)" % ", ".join(map(str, client_open)) if client_open else ""),
                     who=last_ap.name if last_ap else (step_of["approved"].by if ok9 and "approved" in step_of else ""),
                     when=last_ap.at if last_ap else (step_of["approved"].at if ok9 and "approved" in step_of else None),
                     url="/orders/%s/work#client" % ref, label="See client answers"))
    # 10: quotation changes (only when there are some)
    if quotes:
        open_q = [q for q in quotes if q.status == "open"]
        out.append(_step(10, "Quotation changes all handled", not open_q, note="%d open" % len(open_q) if open_q else "%d handled" % len(quotes),
                         block="%d quotation change%s to handle" % (len(open_q), "" if len(open_q) == 1 else "s") if open_q else "",
                         url="/orders/%s/changes" % ref, label="Handle changes"))
    # 11-15: production
    miss = [w for w, v in (("job order no.", o.job_order), ("delivery date", getattr(o, "delivery_date", ""))) if not (v or "").strip()]
    out.append(_step(11, "Job order no. and delivery date (from Odoo)", not miss, note="" if miss else "%s · %s" % (o.job_order, o.delivery_date),
                     block=(lambda x: x[:1].upper() + x[1:])("%s missing in Odoo" % " and ".join(miss)) if miss else ""))
    issued_lines = [l for l in lines if l.last_rev]
    prod = [l for l in issued_lines if any(x.kind == "PRODUCTION" and x.revision_id == l.last_rev.id and x.files for x in l.sets)]
    sets = [x for l in issued_lines for x in l.sets if x.kind == "PRODUCTION"]
    last_set = max(sets, key=lambda x: x.made_at.replace(tzinfo=None)) if sets else None
    out.append(_step(12, "Production drawings made", bool(issued_lines) and len(prod) == len(issued_lines), note="%d of %d products" % (len(prod), len(issued_lines)),
                     who=last_set.made_by if last_set and len(prod) == len(issued_lines) else "", when=last_set.made_at if last_set and len(prod) == len(issued_lines) else None,
                     url="/orders/%s#sets" % ref, label="Make production drawings"))
    if any(x.kind == "VENDOR" for l in issued_lines for x in l.sets):
        vend = [l for l in issued_lines if any(x.kind == "VENDOR" and x.revision_id == l.last_rev.id for x in l.sets)]
        out.append(_step(12, "Subcontractor drawings made", len(vend) == len(issued_lines), note="%d of %d products" % (len(vend), len(issued_lines)),
                         url="/orders/%s#sets" % ref, label="Make subcontractor drawings"))
    last_issue = st_issue.at if st_issue else None
    bom_ok = [b for b in boms if not last_issue or b.made_at.replace(tzinfo=None) >= last_issue.replace(tzinfo=None)]
    out.append(_step(13, "Bill of material and cut list made", bool(bom_ok), note="panel-saw cutting" if bom_ok and bom_ok[-1].panel_saw else "",
                     who=bom_ok[-1].made_by if bom_ok else "", when=bom_ok[-1].made_at if bom_ok else None, url="/orders/%s#sets" % ref, label="Make the BOM"))
    rel = step_of.get("released")
    out.append(_step(14, "Released to production (P1)", o.stage in ("production", "completed"), who=rel.by if rel else "", when=rel.at if rel else None,
                     url="/orders/%s" % ref, label="Release"))
    comp = step_of.get("completed")
    out.append(_step(15, "Project marked completed", o.stage == "completed", who=comp.by if comp and o.stage == "completed" else "",
                     when=comp.at if comp and o.stage == "completed" else None, url="/orders/%s" % ref, label="Mark completed"))
    if not production:
        out = [x for x in out if x["title"] not in PRODUCTION_STEPS]
    # number them in order, then the states: done; the first not-done (non-optional) step is now / blocked; the rest later
    current = None
    for i, x in enumerate(out, 1):
        x["no"] = i
        if x["done"]:
            x["state"] = "done"
        elif x["optional"]:
            x["state"] = "optional"
        elif current is None:
            x["state"] = "blocked" if x["block"] else "now"
            current = x
        else:
            x["state"] = "blocked" if x["block"] and x["title"].startswith("Quotation changes") else "later"   # a lock shows even later on
    return out


def summary(items):
    """-> (current step or None when all done, its number, total)."""
    cur = next((x for x in items if x["state"] in NOW_STATES), None)
    return cur, (cur["no"] if cur else len(items)), len(items)


def involved(o, user):
    """Whose projects show in "My next steps": administrators and sales coordinators see every project; others the ones
    they are the salesperson of or worked on (answered, drew or issued)."""
    if user.role in ("admin", "admin2", "coordinator"):
        return True
    if o.salesperson and o.salesperson == user.name:
        return True
    for l in o.lines:
        if any(q.answered_by == user.name for q in l.questions) or (l.drawing and l.drawing.made_by == user.name) or any(r.issued_by == user.name for r in l.revisions):
            return True
    return False
