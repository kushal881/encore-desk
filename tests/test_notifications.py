"""Notifications (issue #48): opening never clears; resolving the thing clears it for everyone; each person sees only
their own; earlier history does not flood the bell; the count matches the list."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
from fastapi.testclient import TestClient
from desk.app import app
from desk import db

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def mine(name, open_only=True):
    with db.Session() as s:
        q = s.query(db.Notification).filter(db.Notification.user_name == name, db.Notification.source != "_start")
        if open_only:
            q = q.filter(db.Notification.resolved_at.is_(None))
        return {n.source: n for n in q}


def bell_count(html):
    m = re.search(r'aria-label="Notifications: (\d+) open"', html)
    return int(m.group(1)) if m else None


def test_notifications():
    as_(2); c.get(A)
    with db.Session() as s:                                            # history from before: an approval and a line with an issued revision
        o = s.query(db.Order).one(); l1, l2 = o.lines[0], o.lines[1]
        r1 = db.Revision(line_id=l1.id, number="Encore-Nov-2026-05-01", rev="A", spec={}, files={}, issued_by="Sushil Singhal")
        r2 = db.Revision(line_id=l2.id, number="Encore-Nov-2026-05-02", rev="A", spec={}, files={}, issued_by="Sushil Singhal")
        s.add_all([r1, r2]); s.flush()
        s.add(db.Approval(order_id=o.id, revision_id=r1.id, outcome="approved", name="Mr Singh", how="phone"))
        s.query(db.Notification).delete(); s.commit()                  # as on the day this is deployed: the bell starts now
        rid2, oid = r2.id, o.id
    as_(1)
    h = c.get("/").text
    assert bell_count(h) == 0 and not mine("Kushal Singhal")             # earlier history: nothing
    # the client asks for changes on Product 2: the team gets it (admin, Sushil who issued it, Anchal the order's salesperson)
    with db.Session() as s:
        s.add(db.Approval(order_id=oid, revision_id=rid2, outcome="changes", name="Mr Singh", how="portal", note="[1] 1050 long")); s.commit()
    h = c.get("/").text
    assert bell_count(h) == 1 and "client asked for changes" in h
    assert len(mine("Sushil Singhal")) == 1 and len(mine("Anchal Dawar")) == 1
    n = next(iter(mine("Kushal Singhal").values()))
    # opening marks it seen but does not clear it
    r = c.get("/notifications/%d/open" % n.id, follow_redirects=False)
    assert r.headers["location"] == A + "/work#client"
    assert bell_count(c.get("/").text) == 1 and mine("Kushal Singhal")[n.source].seen_at is not None
    assert c.get("/notifications/%d/open" % mine("Sushil Singhal")[n.source].id).status_code == 404     # someone else's
    # resolving the thing (declined on the desk) clears it for everyone, into Resolved
    with db.Session() as s:
        ap = s.query(db.Approval).filter_by(outcome="changes").one()
        s.add(db.ClientAction(order_id=oid, approval_id=ap.id, kind="reject", text="stays", by="Kushal Singhal")); s.commit()
    assert bell_count(c.get("/").text) == 0 and not mine("Sushil Singhal") and not mine("Anchal Dawar")
    assert "client asked for changes" in c.get("/notifications?show=resolved").text
    # a new client approval (after the bell started) is a note: resolved when opened
    with db.Session() as s:
        s.add(db.Approval(order_id=oid, revision_id=rid2, outcome="approved", name="Mr Singh", how="phone")); s.commit()
    c.get("/")
    n = next(v for k, v in mine("Kushal Singhal").items() if k.startswith("approved:"))
    c.get("/notifications/%d/open" % n.id)
    assert "approved:" not in "".join(mine("Kushal Singhal"))
    # requests waiting for a decision: the administrators; the secondary administrator only for what he may decide
    with db.Session() as s:
        s.add(db.ChangeRequest(kind="after_approval", order_ref="Q-2026-10-444", line_no=1, text="unlock", asked_by="Anchal Dawar"))
        s.add(db.ChangeRequest(kind="revision", order_ref="Q-2026-10-444", line_no=1, text="rev", asked_by="Anchal Dawar"))
        s.add(db.EngineRequest(order_ref="Q-2026-10-444", product="Product 1", text="glass top", asked_by="Anchal Dawar")); s.commit()
    c.get("/")
    k = {x.split(":")[0] for x in mine("Kushal Singhal")}
    su = mine("Sushil Singhal")
    assert {"change", "engine"} <= k and len([x for x in su if x.startswith("change:")]) == 1 and not any(x.startswith("engine:") for x in su)
    # a decision makes a task for the person who asked: theirs only, until they mark it done
    c.post("/approvals/engine/1/reject", data={"note": "no"})
    as_(3)
    h = c.get("/").text
    t = [x for x in mine("Anchal Dawar") if x.startswith("task:")]
    assert len(t) == 1 and bell_count(h) == len(mine("Anchal Dawar"))
    tid = int(t[0].split(":")[1])
    c.post("/tasks/%d/done" % tid)
    c.get("/")
    assert not [x for x in mine("Anchal Dawar") if x.startswith("task:")]
    # an open quotation change: the team; handled -> resolved
    with db.Session() as s:
        s.add(db.QuoteChange(order_id=oid, version=2, kind="qty", line_no=1, name="Desk", stage="issued")); s.commit()
    as_(1)
    assert any(x.startswith("quote:") for x in (c.get("/") and mine("Kushal Singhal")))
    with db.Session() as s:
        s.query(db.QuoteChange).one().status = "handled"; s.commit()
    c.get("/")
    assert not any(x.startswith("quote:") for x in mine("Kushal Singhal"))
    # the page: Open / Resolved, by project; the count matches the list
    h = c.get("/notifications").text
    assert bell_count(h) == len(mine("Kushal Singhal")) == len(re.findall(r'<span style="flex:1 1 auto">', h))
    assert "All projects" in h and "Q-2026-10-444" in c.get("/notifications?show=resolved&ref=Q-2026-10-444").text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
