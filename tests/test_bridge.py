"""Bridge products (issue #73): a drawing made outside the engine goes through the normal flow, marked BRIDGE for staff.
Covers: who may upload, no bridge without an engine request, no issue without the admin's hand-check tick, normal drawing
numbers and revisions (a new upload needs the tick again and becomes Rev B), the client portal shows it as a normal
drawing, the viewer has no AI change panel, Redraw previews leaves it alone, production copies say BRIDGE, the BOM leaves
it out, an approved (locked) bridge drawing cannot be replaced, and Redraw on the engine clears BRIDGE. Needs ENGINES_DIR."""
import os, sys, tempfile, re, io
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest, pymupdf
from fastapi.testclient import TestClient
from desk.app import app, load
from desk import db, engines, issuing, bridge

A = "/orders/Q-2026-10-444"
c = TestClient(app)
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))


def pdf_bytes(text, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=842, height=595).insert_text((60, 80), "%s sheet %d" % (text, i + 1), fontsize=20)
    return doc.tobytes()


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def upload(no, text="KITCHEN", **form):
    files = {"pdf": ("kitchen.pdf", pdf_bytes(text), "application/pdf"), "bom": ("kitchen-bom.xlsx", b"PK fake workbook", "application/octet-stream")}
    return c.post("%s/bridge/%d" % (A, no), data=form, files=files, follow_redirects=False)


def line(no):
    return next(l for l in load("Q-2026-10-444").lines if l.no == no)


def text_of(path):
    with pymupdf.open(path) as d:
        return " ".join(p.get_text() for p in d)


def test_bridge_flow():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:
        s.add(db.User(email="sales.ravi@encoreprimefurniture.com", name="Ravi", role="sales")); s.commit()
        sales_id = s.query(db.User).filter_by(name="Ravi").one().id
    as_(1); c.get(A)

    # empty / missing values and wrong people are refused
    as_(sales_id)
    assert upload(2, request_text="Kitchen").status_code == 403
    as_(3)                                                                     # coordinator may upload
    assert upload(2).status_code == 400                                        # no engine request: refused
    assert c.post(A + "/bridge/2", data={"request_text": "Kitchen"}, files={"pdf": ("x.txt", b"hello", "text/plain")}).status_code == 400
    assert c.post(A + "/bridge/2", data={"request_text": "Kitchen"}, files={"pdf": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    r = upload(2, request_text="Add the modular kitchen to the engine")
    assert r.status_code == 303
    with db.Session() as s:
        rq = s.query(db.EngineRequest).one()
        assert rq.order_ref == "Q-2026-10-444" and "kitchen" in rq.text and rq.asked_by == "Anchal Dawar"
    l = line(2)
    assert l.bridge and not l.bridge_checked and l.open_count == 0 and l.drawing.engine_version == "BRIDGE"
    page = c.get(A + "/work").text
    assert "BRIDGE drawing v1" in page and "Waiting for an administrator" in page and "/bridge/drawings/%d/bom" % l.drawing.id in page
    assert c.get("/bridge/drawings/%d/bom" % l.drawing.id).content == b"PK fake workbook"

    # an AI change waiting on another product does not matter; the bridge needs the hand check before issue
    with db.Session() as s:
        l1 = s.query(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444", db.OrderLine.no == 1).one()
        s.add(db.AiTurn(line_id=l1.id, typed="x", kind="change", status="trial")); s.commit()
    r = c.post(A + "/issue", data={"folder_no": "7", "nos": "2", "back": "issue"})
    assert r.status_code == 400
    assert c.post(A + "/bridge/2/check").status_code == 403                    # only an administrator ticks
    as_(2)                                                                     # Sushil, secondary administrator
    c.post(A + "/bridge/2/check")
    assert c.post(A + "/bridge/2/check").status_code == 400                    # once
    assert line(2).bridge_checked
    c.post(A + "/issue", data={"folder_no": "7", "nos": "2", "back": "issue"})
    with db.Session() as s:
        rv = s.query(db.Revision).one()
        assert rv.engine_version == "BRIDGE" and rv.rev == "A" and re.fullmatch(r"Encore-[A-Z][a-z]{2}-\d{4}-07-01", rv.number)
        assert rv.files["pages"] == 2 and rv.files.get("bom")
        t = text_of(rv.files["pdf"])
        assert "KITCHEN sheet 1" in t and rv.number in t and "Rev A" in t and "Sheet 2 of 2" in t   # Encore's number on every sheet
        assert "BRIDGE" not in t                                               # the client sees a normal drawing
        rid = rv.id
    assert line(2).issued_current

    # the viewer: no AI change panel on a bridge drawing
    v = c.get(A + "/view/2").text
    assert "bridge drawing made outside the engine" in v

    # Redraw previews leaves the bridge drawing alone
    c.post(A + "/draw")
    assert line(2).bridge and line(2).drawing.spec["bridge"]["upload"] == 1

    # a new version needs the tick again, and is issued as Rev B with the same number
    as_(3); upload(2, text="KITCHEN V2", request_id=str(rq.id))
    l = line(2)
    assert l.drawing.spec["bridge"]["upload"] == 2 and not l.bridge_checked and not l.issued_current
    as_(1); c.post(A + "/bridge/2/check"); c.post(A + "/issue", data={"folder_no": "7", "nos": "2", "back": "issue"})
    with db.Session() as s:
        revs = s.query(db.Revision).order_by(db.Revision.id).all()
        assert [x.rev for x in revs] == ["A", "B"] and revs[0].number == revs[1].number and "KITCHEN V2" in text_of(revs[1].files["pdf"])
        rid = revs[1].id

    # client portal: shown like any drawing
    c.post(A + "/access", data={"email": "chef@client.in", "can": "approve"})
    p = TestClient(app)
    p.post("/portal/code", data={"email": "chef@client.in"})
    code = re.search(r"(\d{6})", next(m for m in reversed(MAIL) if m["to"] == ["chef@client.in"])["subject"]).group(1)
    p.post("/portal/signin", data={"email": "chef@client.in", "code": code})
    h = p.get("/portal/orders/Q-2026-10-444").text
    assert "BRIDGE" not in h and "/d/%d" % rid in h
    assert p.get("/portal/orders/Q-2026-10-444/d/%d/pdf" % rid).status_code == 200

    # production: approved and released; production copies carry the BRIDGE band; the BOM leaves it out
    with db.Session() as s:
        oid = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().id
        for k in ("sent", "approved", "released"):
            s.add(db.Step(order_id=oid, kind=k, by="test"))
        s.commit()
    c.post(A + "/sets/PRODUCTION", data={"deadline": "30 DEC 2026"})
    with db.Session() as s:
        ps = s.query(db.ProductionSet).filter_by(kind="PRODUCTION").order_by(db.ProductionSet.id.desc()).first()
        assert ps.engine_version == "BRIDGE" and "BRIDGE - made outside the engine, check manually" in text_of(ps.files[0]["pdf"])
    assert "BRIDGE — made outside the engine" in c.get(A + "/production/2").text
    r = c.post(A + "/production/2/ai", data={"text": "add a shelf"}, follow_redirects=False)
    assert "bridge" in r.headers["location"].lower()
    c.post(A + "/bom")
    with db.Session() as s:
        msg = s.query(db.Message).filter(db.Message.order_id == oid).order_by(db.Message.id.desc()).first().text
        assert "every issued product is a bridge drawing" in msg and "Product 2" in msg

    # BRIDGE marked on Projects and on the engine requests page / dashboard for directors
    assert "BRIDGE" in c.get("/projects").text
    assert "Bridge products waiting for the engine" in c.get("/").text and "Q-2026-10-444" in c.get("/engine-requests").text

    # approved by the client: locked, a new upload is refused
    with db.Session() as s:
        s.add(db.Approval(order_id=oid, revision_id=rid, outcome="approved", name="Chef", how="email", on="28 NOV 2026")); s.commit()
    as_(3)
    r = upload(2, request_id=str(rq.id))
    assert r.status_code == 400 and "locked" in r.text


def test_redraw_on_engine():
    """Once the engine can draw the product, Redraw on the engine makes an engine preview and clears BRIDGE."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:                                                   # unlock product 2 for this test (Kushal's permission)
        s.query(db.Approval).delete(); s.commit()
    as_(1)
    l = line(2)
    if any(not q.answer for q in l.questions):
        assert c.post(A + "/bridge/2/redraw").status_code == 400              # questions first
        with db.Session() as s:
            qs = [q.id for q in s.query(db.Question).filter(db.Question.line_id == l.id) if not q.answer]
        for qid in qs:
            q = next(x for x in l.questions if x.id == qid)
            c.post("%s/answer/%d" % (A, qid), data={"value": (q.options or ["1"])[0]})
    r = c.post(A + "/bridge/2/redraw", follow_redirects=False)
    assert r.status_code == 303
    l = line(2)
    assert not l.bridge and l.drawing.engine_version != "BRIDGE" and l.drawing.status == "preview" and not l.issued_current
    with db.Session() as s:
        assert any("BRIDGE cleared" in m.text for m in s.query(db.Message))


def test_old_data_and_empty():
    """Drawings from before this change have no bridge record: not bridge, nothing changes for them."""
    d = db.Drawing(engine_version="v11", spec={}, files={})
    assert not bridge.checked(d) and bridge.info(d) == {} and bridge.info(None) == {}
    class L: drawing = d
    assert not bridge.is_bridge(L())
    class N: drawing = None
    assert not bridge.is_bridge(N())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
