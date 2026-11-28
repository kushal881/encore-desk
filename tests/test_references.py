"""Client references (issue #72): upload on the order and on a product, wrong type / too big refused, open through the desk
only (logged), production sees only what is ticked for production, never on the portal unless ticked, a client's attachment
to a change request joins the list, the AI gets the pictures only when ticked, and old projects show an empty list."""
import os, sys, tempfile, re, io
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest, pymupdf
from PIL import Image
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, issuing, references, assistant

A = "/orders/Q-2026-10-444"
c = TestClient(app)
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, subject=subject, body=body)))


def jpg():
    b = io.BytesIO(); Image.new("RGB", (800, 600), (200, 180, 160)).save(b, "JPEG"); return b.getvalue()


def as_(uid):
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def up(name, data, **form):
    return c.post(A + "/references", data=form, files={"file": (name, data, "application/octet-stream")}, follow_redirects=False)


def test_references():
    as_(1); c.get(A)
    # old projects (and this one before any upload): an empty list
    assert "No sketches, photos or files from the client yet" in c.get(A).text
    with db.Session() as s:
        oid = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().id
        s.add(db.User(email="production@encoreprimefurniture.com", name="Prod", role="production"))
        s.add(db.User(email="sales.ravi@encoreprimefurniture.com", name="Ravi", role="sales")); s.commit()
        prod_id = s.query(db.User).filter_by(name="Prod").one().id
        sales_id = s.query(db.User).filter_by(name="Ravi").one().id
    assert references.listed(oid) == []

    # empty and wrong values refused
    assert c.post(A + "/references", data={"note": "x"}).status_code == 400                               # no file
    assert up("notes.docx", b"PK..").status_code == 400                                                    # wrong type
    assert up("empty.pdf", b"").status_code == 400
    assert up("big.pdf", b"0" * (25 * 1024 * 1024 + 10)).status_code == 400                               # over 25 MB
    assert up("s.jpg", jpg(), line_no="9").status_code == 400                                              # no product 9

    # a salesperson adds the hand sketch to Product 2; a designer adds a site PDF for the whole order
    as_(sales_id)
    assert up("sketch.jpg", jpg(), note="client hand sketch, kitchen wall A", line_no="2").status_code == 303
    doc = pymupdf.open(); doc.new_page().insert_text((72, 72), "Site measurements"); pdf = doc.tobytes()
    as_(3)
    assert up("site.pdf", pdf, note="site measurements").status_code == 303
    assert up("plan.dwg", b"AC1027 dwg bytes", line_no="1", for_production="1").status_code == 303
    rs = references.listed(oid)
    assert [(r.name, r.line_no, r.kind) for r in rs] == [("sketch.jpg", 2, "image"), ("site.pdf", 0, "pdf"), ("plan.dwg", 1, "cad")]
    assert rs[0].thumb and rs[1].thumb and not rs[2].thumb and rs[2].for_production
    sk, site, dwg = rs

    # shown on Specify (product's and whole order's), the project page and the viewer's side panel
    sp = c.get(A + "/specify/2").text
    assert "sketch.jpg" in sp and "site.pdf" in sp and "plan.dwg" not in sp
    assert all(n in c.get(A).text for n in ("sketch.jpg", "site.pdf", "plan.dwg"))
    # opened through the desk only, logged
    assert c.get("/references/%d" % sk.id).content[:2] == b"\xff\xd8"
    assert c.get("/references/%d/thumb" % sk.id).status_code == 200
    with db.Session() as s:
        assert s.query(db.FileLog).filter(db.FileLog.name == "sketch.jpg").count() == 1
    assert TestClient(app).get("/references/%d" % sk.id, follow_redirects=False).status_code in (302, 303, 401)   # signed out: no
    # production sees only what is ticked for production
    as_(prod_id)
    assert c.get("/references/%d" % sk.id).status_code == 404 and c.get("/references/%d" % dwg.id).status_code == 200
    assert up("x.jpg", jpg()).status_code == 403

    # never on the portal unless ticked "show to client"
    as_(1)
    c.post(A + "/access", data={"email": "chef@client.in", "can": "approve"})
    p = TestClient(app)
    p.post("/portal/code", data={"email": "chef@client.in"})
    code = re.search(r"(\d{6})", next(m for m in reversed(MAIL) if m["to"] == ["chef@client.in"])["subject"]).group(1)
    p.post("/portal/signin", data={"email": "chef@client.in", "code": code})
    assert "sketch.jpg" not in p.get("/portal/orders/Q-2026-10-444").text
    assert p.get("/portal/orders/Q-2026-10-444/references/%d" % sk.id).status_code == 404
    c.post("/references/%d/flags" % sk.id, data={"show_client": "1"})
    assert "sketch.jpg" in p.get("/portal/orders/Q-2026-10-444").text
    assert p.get("/portal/orders/Q-2026-10-444/references/%d" % sk.id).status_code == 200
    assert p.get("/portal/orders/Q-2026-10-444/references/%d" % site.id).status_code == 404
    with db.Session() as s:
        assert any("shown to the client" in m.text for m in s.query(db.Message).filter_by(order_id=oid))

    # remove: the person who added it or an administrator
    as_(sales_id)
    assert c.post("/references/%d/remove" % site.id).status_code == 403
    as_(1)
    c.post("/references/%d/remove" % site.id)
    assert [r.name for r in references.listed(oid)] == ["sketch.jpg", "plan.dwg"]


def test_ai_reads_pictures_only_when_ticked(monkeypatch):
    """The AI gets the product's reference pictures only when staff tick it; with an AI change still waiting, nothing new."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    seen = []
    monkeypatch.setattr(assistant, "ask", lambda text, product, model, markers, edir, history=(), images=(): (seen.append(list(images)),
                        dict(kind="answer", said="", values={}, problems=[], answer="The sketch shows 3 modules."))[1])
    as_(1)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question).join(db.OrderLine).join(db.Order).filter(db.Order.source_ref == "Q-2026-10-444")}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    v = c.get(A + "/view/2").text
    assert "Let the AI read the client" in v and "sketch.jpg" in v                                       # side panel + tick box
    c.post(A + "/view/2/ai", data={"text": "how many modules?"})
    c.post(A + "/view/2/ai", data={"text": "read the module widths from the sketch", "use_refs": "1"})
    assert seen[0] == [] and len(seen[1]) == 1 and seen[1][0][0] == "image/jpeg"
    assert "Let the AI read the client" not in c.get(A + "/view/1").text                                  # product 1 has no picture


def test_portal_attachment_joins_the_list():
    """A file the client attaches to a change request on the portal lands in the references, shown to the client."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    as_(1)
    c.post(A + "/issue", data={"folder_no": "8", "nos": "2", "back": "issue"})
    with db.Session() as s:
        rid = s.query(db.Revision).order_by(db.Revision.id.desc()).first().id
        oid = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one().id
    p = TestClient(app)
    p.post("/portal/code", data={"email": "chef@client.in"})
    code = re.search(r"(\d{6})", next(m for m in reversed(MAIL) if m["to"] == ["chef@client.in"])["subject"]).group(1)
    p.post("/portal/signin", data={"email": "chef@client.in", "code": code})
    p.post("/portal/orders/Q-2026-10-444/d/%d/changes" % rid, data={"note": "see my photo"}, files={"file": ("wall.jpg", jpg(), "image/jpeg")})
    r = references.listed(oid)[-1]
    assert r.name == "wall.jpg" and r.source == "portal" and r.show_client and r.line_no == 2 and r.by == "Client: chef@client.in"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
