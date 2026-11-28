"""Stop alerts (decision 12): when a quotation changes after production started, or a released production drawing is
replaced (P1 -> P2). Sent to the administrators (Kushal, Sushil), the project's salesperson and the production users:
a task in the desk for each, and an email from the company address. A failed email never stops the alert; the desk
says in the project history who got it and who did not."""
from sqlalchemy import select
from . import db, auth, issuing


def recipients(s, order):
    """-> [(name, email or '')] for one order: administrators, secondary administrators, production users, salesperson."""
    users = list(s.scalars(select(db.User).where(db.User.active == True)))                     # noqa: E712
    picked = {u.name: u.email for u in users if u.role in ("admin", "production") or (u.email or "").lower() in auth.SECONDARY}
    if order.salesperson:
        u = next((u for u in users if u.name.strip().lower() == order.salesperson.strip().lower()), None)
        picked[order.salesperson] = u.email if u else ""
    return sorted(picked.items())


def stop(s, order, line_no, text, source, base_url=""):
    """Adds the tasks and the history line, and emails everyone with an address. -> the names it went to."""
    people = recipients(s, order)
    for name, _ in people:
        s.add(db.Task(assignee=name, order_ref=order.source_ref, line_no=line_no, text=text, source=source))
    mails = sorted({e for _, e in people if e})
    no_mail = [n for n, e in people if not e]
    err = ""
    if mails:
        link = "%s/orders/%s" % (base_url.rstrip("/"), order.source_ref) if base_url else ""
        try:
            issuing.send(mails, [], "STOP: %s · %s" % (order.source_ref, order.client.name if order.client else ""),
                         text + ("\n\nOpen the project: %s" % link if link else "") + "\n\nEncore drawing desk")
        except RuntimeError as e:
            err = str(e)
    parts = ["Stop alert sent to %s" % ", ".join(n for n, _ in people)]
    if mails:
        parts.append("the email could not be sent (%s)" % err if err else "emailed to %s" % ", ".join(mails))
    if no_mail:
        parts.append("no email address for %s" % ", ".join(no_mail))
    s.add(db.Message(order_id=order.id, who="desk", text="; ".join(parts) + "."))
    return [n for n, _ in people]
