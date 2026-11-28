"""Screen 8: quotation changes. Saved quotation versions, the change matrix by drawing stage (UI_BRIEF), the lock on issue
and production, Cancelled stamp, revision raising (a qty-only change after issue raises a revision, decision 4), the stop
alert in production (decision 12), Quotation needs update, and the Odoo webhook (decision 2). Odoo is stood in for by the
sample order, changed between reads. Drawing parts need ENGINES_DIR."""
import os, sys, tempfile, copy
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
import desk.app as appmod
from desk.app import app
from desk import db, engines, quotes as qt
from desk.sources.sample import SampleSource
from desk.sources.base import SourceLine

c = TestClient(app)
A = "/orders/Q-2026-10-444"
KUSHAL, SUSHIL, ANCHAL = 1, 2, 3
ODOO = {"order": SampleSource().get_order("Q-2026-10-444")}             # what "Odoo" says now


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


@pytest.fixture(autouse=True)
def fake_odoo(monkeypatch):
    real = appmod.SOURCE.get_order
    monkeypatch.setattr(appmod.SOURCE, "get_order", lambda ref: copy.deepcopy(ODOO["order"]) if ref == "Q-2026-10-444" else real(ref))


def line(ref):
    return next(l for l in ODOO["order"].lines if l.line_ref == ref)


def L(ref, name="X", qty=1, price=0, nd="", desc=""):
    return dict(ref=ref, name=name, description=desc, qty=qty, price=price, no_drawing=nd)


def test_diff():
    old = [L("1", "Desk", 2, 100), L("2", "Storage"), L("3", "Installation", nd="installation"), L("4", "Pedestal")]
    new = [L("1", "Desk", 3, 100), L("2", "Storage", price=50), L("5", "Chair", nd="bought-out"), L("4", "Pedestal", desc="castors"), L("6", "Table")]
    got = {(d["kind"], d["ref"]) for d in qt.diff(old, new)}
    assert got == {("qty", "1"), ("price", "2"), ("spec", "4"), ("added", "6")}                      # installation gone / chair added: no change
    assert {(d["kind"], d["ref"]) for d in qt.diff([L("1", "Desk")], [L("1", "Desk", nd="bought-out")])} == {("dropped", "1")}
    assert {(d["kind"]) for d in qt.diff([L("", "Desk", 1)], [L("", "Desk", 2)])} == {"qty"}         # old versions without ids: by position
    assert qt.diff([], []) == [] and qt.diff([L("1")], [L("1")]) == []


def test_changes_before_drawing():
    as_(SUSHIL)
    c.get(A)
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        assert [v.n for v in s.query(db.QuoteVersion).filter_by(order_id=o.id)] == [1] and all(l.source_line for l in o.lines)
    assert qt.check("Q-2026-10-444") == (1, [])                                                    # nothing changed
    assert "No changes. The drawings match Odoo version 1" in c.get(A + "/changes").text
    # Odoo v2: qty of product 1, product 2 dropped, a new pedestal, a price change on product 3, installation added
    line("sample-1").qty = 2
    ODOO["order"].lines = [l for l in ODOO["order"].lines if l.line_ref != "sample-2"]
    line("sample-3").price = 999
    ODOO["order"].lines.append(SourceLine(no=0, name="Pedestal 1 Drawer 1 Shutter", description="", qty=2, line_ref="sample-9"))
    ODOO["order"].lines.append(SourceLine(no=0, name="Installation and transportation", description="", qty=1, line_ref="sample-10", no_drawing="installation"))
    n, ids = qt.check("Q-2026-10-444", "webhook")
    assert n == 2 and len(ids) == 4
    with db.Session() as s:
        ch = {x.kind: x for x in s.query(db.QuoteChange)}
    assert ch["price"].status == "ignored" and ch["qty"].stage == "not drawn" and ch["qty"].line_no == 1 and ch["dropped"].line_no == 2
    h = c.get(A).text
    assert "3 changes to handle" in h and "Issue and production are locked" in h
    h = c.get(A + "/changes").text
    assert "Changes inbox · 3 to handle" in h and "use the new values" in h and "remove it from the drawings" in h and "Price only" in h
    assert "Odoo version 2 found · webhook" in h
    r = c.post(A + "/issue", data={"folder_no": "5"})
    assert r.status_code == 400 and "Handle the 3 quotation changes first" in r.text
    # handle them
    c.post(A + "/changes/%d/update" % ch["qty"].id)
    c.post(A + "/changes/%d/remove" % ch["dropped"].id)
    c.post(A + "/changes/%d/add" % ch["added"].id)
    assert c.post(A + "/changes/%d/add" % ch["added"].id).status_code == 400                      # handled once only
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        lines = {l.no: l for l in o.lines}
        assert lines[1].qty == 2 and 2 not in lines and lines[4].source_line == "sample-9" and lines[4].qty == 2
        assert any(x.no == 2 and x.reason == "Dropped from the quotation" for x in o.not_drawn)
    assert "Changes inbox · 0 to handle" in c.get(A + "/changes").text and "to handle" not in c.get(A).text.split("Quotation changes")[0][-200:]
    # Quotation needs update: a product added on the desk
    assert c.post(A + "/changes/notify-sales").status_code == 400
    c.post(A + "/lines/add", data={"name": "Side table", "qty": "1"})
    c.post(A + "/changes/notify-sales")
    with db.Session() as s:
        assert any(t.text.startswith("Quotation needs update (Q-2026-10-444): Product 5 · Side table") for t in s.query(db.Task))


def answer_all():
    with db.Session() as s:
        qs = [(q.id, q.key) for q in s.query(db.Question).filter(db.Question.answer == "")]
    vals = dict(note="Black bracket", size="L1200 D450 H750", shelves="1", hinge="Right")
    for qid, key in qs:
        c.post("%s/answer/%d" % (A, qid), data={"value": vals.get(key, "1")})


def test_changes_after_issue():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(SUSHIL)
    c.post(A + "/lines/5/remove", data={"reason": "Other"})                                       # the side table: not needed
    answer_all()
    c.post(A + "/draw")
    c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    with db.Session() as s:
        revs = {l.no: l.revisions[-1] for l in s.query(db.OrderLine) if l.revisions}
    assert set(revs) == {1, 3, 4}
    # issued: a qty-only change raises a revision (decision 4); an AI change left waiting still blocks the issue
    line("sample-3").qty = 3
    qt.check("Q-2026-10-444")
    with db.Session() as s:
        qc = s.query(db.QuoteChange).filter_by(status="open").one()
        l3 = s.query(db.OrderLine).filter_by(no=3).one()
        s.add(db.AiTurn(line_id=l3.id, typed="600 deep", kind="change", status="trial")); s.commit()
        tid = s.query(db.AiTurn).filter_by(status="trial").one().id
    assert qc.stage == "issued" and "new revision" in c.get(A + "/changes").text
    c.post(A + "/changes/%d/update" % qc.id)
    r = c.post(A + "/issue", data={"nos": "3", "back": "issue"})
    assert r.status_code == 400 and "Accept or Undo" in r.text
    c.post("%s/view/3/ai/%d/undo" % (A, tid))
    c.post(A + "/issue", data={"nos": "3", "back": "issue"})
    with db.Session() as s:
        l3 = s.query(db.OrderLine).filter_by(no=3).one()
        assert [r.rev for r in l3.revisions] == ["A", "B"] and l3.qty == 3
    # issued and dropped: Cancelled stamp, serial retired, left out of the combined set
    ODOO["order"].lines = [l for l in ODOO["order"].lines if l.line_ref != "sample-9"]
    qt.check("Q-2026-10-444")
    with db.Session() as s:
        qc = s.query(db.QuoteChange).filter_by(status="open").one()
    assert qc.kind == "dropped" and qc.stage == "issued"
    c.post(A + "/changes/%d/cancel" % qc.id)
    with db.Session() as s:
        l4 = s.query(db.OrderLine).filter_by(no=4).one()
        assert l4.cancelled_by == "Sushil Singhal" and os.path.exists(l4.revisions[-1].files["cancelled_pdf"])
    import pymupdf
    assert "CANCELLED" in pymupdf.open(l4.revisions[-1].files["cancelled_pdf"])[0].get_text()
    o = appmod.load("Q-2026-10-444")
    assert [l.no for l, _ in appmod._current(o)] == [1, 3]
    # client approved: locked; staff ask permission, Kushal allows, then it can be redrawn
    with db.Session() as s:
        r1 = s.query(db.OrderLine).filter_by(no=1).one().revisions[-1].id
    c.post("%s/answer-from-client/%d" % (A, r1), data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "ok"})
    line("sample-1").description = "Laminate: Frosty White"
    qt.check("Q-2026-10-444")
    with db.Session() as s:
        qc = s.query(db.QuoteChange).filter_by(status="open").one()
    assert qc.kind == "spec" and qc.stage == "approved"
    assert c.post(A + "/changes/%d/update" % qc.id).status_code == 403
    c.post(A + "/changes/%d/ask" % qc.id)
    with db.Session() as s:
        cr = s.query(db.ChangeRequest).filter_by(kind="after_approval").one()
    as_(KUSHAL); c.post("/approvals/after_approval/%d/approve" % cr.id); as_(SUSHIL)
    assert c.post(A + "/changes/%d/update" % qc.id, follow_redirects=False).status_code == 303
    with db.Session() as s:
        assert "Frosty White" in s.query(db.OrderLine).filter_by(no=1).one().description
    c.post(A + "/issue", data={"nos": "1", "back": "issue"})
    # in production: a stop alert goes out at once; only the administrator redoes it
    with db.Session() as s:
        for l in s.query(db.OrderLine).filter(db.OrderLine.no.in_([1, 3])):
            s.add(db.Approval(order_id=l.order_id, revision_id=l.revisions[-1].id, outcome="approved", name="Mr Singh", how="phone", note="ok"))
        s.commit()
    as_(KUSHAL); c.post(A + "/step/released", data={"note": "urgent"}); as_(SUSHIL)
    line("sample-3").qty = 4
    qt.check("Q-2026-10-444")
    with db.Session() as s:
        qc = s.query(db.QuoteChange).filter_by(status="open").one()
        alerts = s.query(db.Task).filter(db.Task.source == "quotestop:%d" % qc.id).all()
    assert qc.stage == "production" and qc.note.startswith("Stop alert sent to") and {"Kushal Singhal", "Sushil Singhal"} <= {t.assignee for t in alerts}
    assert "STOP: the quotation changed after production started" in alerts[0].text
    assert c.post(A + "/sets/production", data={"deadline": "10 DEC 2026"}).status_code == 400   # locked while open
    assert c.post(A + "/changes/%d/update" % qc.id).status_code == 403
    as_(KUSHAL)
    c.post(A + "/changes/%d/update" % qc.id)
    with db.Session() as s:
        assert s.query(db.QuoteChange).filter_by(id=qc.id).one().status == "handled"


def test_old_data_and_webhook(monkeypatch):
    as_(SUSHIL)
    with db.Session() as s:                                                    # old data: no saved version, products not tied
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        s.query(db.QuoteVersion).filter_by(order_id=o.id).delete()
        for l in o.lines:
            l.source_line = ""
        s.commit()
    assert qt.check("Q-2026-10-444") == (1, [])                                # the first look is the baseline
    with db.Session() as s:
        assert s.query(db.OrderLine).filter_by(no=1).one().source_line == "sample-1"
    # the webhook
    r = c.post("/webhooks/odoo", json={"name": "Q-2026-10-444"})
    assert r.status_code == 503                                                # no token set on the desk: refused
    monkeypatch.setenv("ODOO_WEBHOOK_TOKEN", "s3cret")
    assert c.post("/webhooks/odoo?token=wrong", json={"name": "Q-2026-10-444"}).status_code == 401
    assert c.post("/webhooks/odoo?token=s3cret", json={}).status_code == 400
    assert c.post("/webhooks/odoo?token=s3cret", json={"name": "Q-NOT-ON-DESK"}).json() == {"ok": True, "ref": "Q-NOT-ON-DESK", "project": False}
    line("sample-1").qty = 7
    j = c.post("/webhooks/odoo?token=s3cret", json={"_model": "sale.order", "_id": 1, "name": "Q-2026-10-444"}).json()
    assert j["ok"] and j["project"] and j["version"] == 2 and j["changes"] == 1
    j = c.post("/webhooks/odoo", headers={"x-desk-token": "s3cret"}, json={"name": "Q-2026-10-444"}).json()
    assert j["changes"] == 0                                                   # nothing new since
    fresh = TestClient(app)                                                    # Odoo is not a signed-in user: no session needed
    assert fresh.post("/webhooks/odoo?token=s3cret", json={"name": "Q-2026-10-444"}, follow_redirects=False).json()["ok"]
    assert fresh.get("/orders/Q-2026-10-444/changes", follow_redirects=False).headers["location"] == "/login"   # the inbox still needs sign-in
    j = c.post("/webhooks/odoo/nightly?token=s3cret").json()
    assert j["ok"] and j["checked"] == 0                                       # only Odoo projects (the samples are not)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
