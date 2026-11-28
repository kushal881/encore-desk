"""Client portal order page for 100 drawings (issue #85): progress line and bar, search, filter chips with counts, area
groups ("No area" when empty), a row per drawing with a small preview, products not issued yet as "Being drawn", tick
boxes and ONE email code approving all the ticked drawings, each with its own approval record.
Covers old orders (no areas, approvals given before the portal), a change still with the designer (cannot be bulk-approved),
an approved drawing (never ticked), an empty area, a wrong or expired code (approves nothing) and 100 drawings. Needs ENGINES_DIR."""
import os, sys, tempfile, re, time, datetime
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, issuing, portal

A, P = "/orders/Q-2026-10-444", "/portal/orders/Q-2026-10-444"
SINGH = "singh@achievers.in"
staff, c = TestClient(app), TestClient(app)
MAIL = []


issuing.send = lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body))   # no real email


def code_for(email):
    m = next(m for m in reversed(MAIL) if m["to"] == [email] and m["subject"].startswith("Your Encore code"))
    return re.search(r"(\d{6})", m["subject"]).group(1)


def approvals():
    with db.Session() as s:
        return [(a.revision_id, a.outcome, a.code_verified) for a in s.query(db.Approval).order_by(db.Approval.id)]


def setup_module():
    if not engines.ENGINES_DIR:
        return
    staff.post("/login/dev/2"); staff.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for k, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        if k in qs:
            staff.post("%s/answer/%d" % (A, qs[k]), data={"value": val})
    staff.post(A + "/draw")
    staff.post(A + "/issue", data={"folder_no": "6", "nos": "1,3", "back": "issue"})
    staff.post(A + "/access", data={"email": SINGH, "name": "Mr Singh", "can": "approve"})
    c.post("/portal/code", data={"email": SINGH})
    c.post("/portal/signin", data={"email": SINGH, "code": code_for(SINGH), "keep": "1"})


def rids():
    with db.Session() as s:
        return [r.id for r in s.query(db.Revision).order_by(db.Revision.id)]


def test_page_old_order_no_area_being_drawn():
    """Old order: no areas -> "No area"; product 2 not issued -> "Being drawn", no tick, no link; progress counts."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    v = c.get(P).text
    assert "3 drawings · 0 approved · 2 waiting for you · 1 being drawn" in v
    assert "No area · 3 drawings" in v and 'role="img" aria-label="0 approved, 0 changes asked, 3 to go"' in v
    assert v.count('name="pick"') == 2 and v.count('data-st="drawing"') == 1
    assert 'data-f="waiting"' in v and "Waiting for you <span class=\"c\">2</span>" in v and "Being drawn <span class=\"c\">1</span>" in v
    assert "Approve 2 · one email code" in v and "Review one by one" in v
    assert "Add or remove a product" in v
    # an area set by the designer shows on the row and as its own group
    with db.Session() as s:
        l3 = s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444", db.OrderLine.no == 3).one()
        s.add(db.LineArea(line_id=l3.id, area="MD cabin", qty=1)); s.commit()
    v = c.get(P).text
    assert "MD cabin · 1 drawing" in v and v.index("MD cabin · 1 drawing") < v.index("No area · 2 drawings")


def test_wrong_or_expired_code_approves_nothing():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    r1, r2 = rids()[:2]
    c.post(P + "/approve", data={"do": "code", "pick": [str(r1)], "name": "Mr Singh"})
    good = code_for(SINGH)
    v = c.post(P + "/approve", data={"do": "approve", "pick": [str(r1), str(r2)], "name": "Mr Singh", "agree": "1",
                                     "code": "000001" if good != "000001" else "000002"}).text
    assert "not right" in v and approvals() == []
    assert v.count('name="pick" value="%d" aria-label' % r1) == 1 and 'value="%d" aria-label="Tick' % r1 in v   # ticks kept
    assert re.search(r'value="%d"[^>]*checked' % r1, v)
    with db.Session() as s:                                                    # expired
        pc = s.query(db.PortalCode).filter_by(purpose="approve", used=False).order_by(db.PortalCode.id.desc()).first()
        pc.expires = db.now() - datetime.timedelta(minutes=1); s.commit()
    v = c.post(P + "/approve", data={"do": "approve", "pick": [str(r1)], "name": "Mr Singh", "agree": "1", "code": good}).text
    assert "expired" in v and approvals() == []


def test_one_code_approves_ticked_only_and_skips_changes():
    """Tick 2, one of them has a change with the designer: only the waiting one is approved, with its own record."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    r1, r2 = rids()[:2]
    with db.Session() as s:                                                    # a change still with the designer on r2
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.add(db.Approval(order_id=o.id, revision_id=r2, outcome="changes", name="Mr Singh", email=SINGH, how="portal", note="wider", by="Portal: " + SINGH))
        s.commit()
    v = c.get(P).text
    assert not re.search(r'name="pick" value="%d"' % r2, v) and "Change asked" in v
    # only r2 ticked (sent by hand): nothing waiting, nothing approved
    c.post(P + "/approve", data={"do": "code", "pick": [str(r2)]})
    assert "nothing waiting" in c.post(P + "/approve", data={"do": "code", "pick": [str(r2)]}).text
    c.post(P + "/approve", data={"do": "code", "pick": [str(r1)]})
    r = c.post(P + "/approve", data={"do": "approve", "pick": [str(r1), str(r2)], "name": "Mr Singh", "designation": "Admin", "agree": "1",
                                     "code": code_for(SINGH)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("done=approved-1")
    ap = [x for x in approvals() if x[1] == "approved"]
    assert ap == [(r1, "approved", True)]
    v = c.get(P).text                                                          # approved: shown, never ticked
    assert not re.search(r'name="pick" value="%d"' % r1, v) and ">Approved</span>" in v and 'id="abar"' not in v


def test_hundred_drawings_load_quickly():
    """100 issued drawings on one order: one page, all rows, groups by area, under a few seconds; one code approves 40."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        r0 = s.get(db.Revision, rids()[0])
        for i in range(100):
            l = db.OrderLine(order_id=o.id, no=100 + i, name="Workstation %d (1200 x 600)" % i, qty=2)
            s.add(l); s.flush()
            if i % 2:
                s.add(db.LineArea(line_id=l.id, area="Floor %d" % (i % 5), qty=2))
            s.add(db.Revision(line_id=l.id, number="Encore-Nov-2026-06-%03d" % (100 + i), rev="A", engine_version=r0.engine_version,
                              spec={"item": {"product_name": "Workstation %d" % i, "qty": 2}}, files=r0.files))
        s.commit()
    t = time.time()
    v = c.get(P).text
    assert time.time() - t < 8
    assert v.count('class="drow"') == 103 and v.count('name="pick"') == 100 and "Floor 1 · " in v and "No area · " in v
    assert 'loading="lazy"' in v
    new = rids()[-100:-60]
    c.post(P + "/approve", data={"do": "code", "pick": [str(x) for x in new]})
    r = c.post(P + "/approve", data={"do": "approve", "pick": [str(x) for x in new], "name": "Mr Singh", "agree": "1", "code": code_for(SINGH)},
               follow_redirects=False)
    assert r.headers["location"].endswith("done=approved-40")
    assert sorted(x[0] for x in approvals() if x[1] == "approved" and x[0] in new) == sorted(new)
    assert "approved · 60 waiting for you" in c.get(P).text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
