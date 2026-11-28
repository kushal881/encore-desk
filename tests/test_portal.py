"""Step 8: the client portal (PortalLogin, PortalHome, PortalSet, PortalDrawing, PortalPast; decisions 7, 10, 11).
Mail is replaced by a stand-in that keeps what was sent, so the one-time codes can be read back. Needs ENGINES_DIR."""
import os, sys, tempfile, re, json, datetime
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
SINGH, ACCOUNTS = "singh@achievers.in", "accounts@achievers.in"
staff = TestClient(app)
MAIL = []


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append(dict(to=to, cc=cc, subject=subject, body=body, att=att, name=name)))


def code_for(email):
    m = next(m for m in reversed(MAIL) if m["to"] == [email] and m["subject"].startswith("Your Encore code"))
    return re.search(r"Your Encore code: (\d{6})", m["subject"]).group(1)


def sign_in(c, email, keep=True):
    c.post("/portal/code", data={"email": email})
    r = c.post("/portal/signin", data={"email": email, "code": code_for(email), "keep": "1" if keep else ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/portal/orders"
    return r


def test_codes():
    with db.Session() as s:
        s.add(db.PortalAccess(order_id=999, email="x@y.in")); s.commit()
    assert portal.new_code("x@y.in", "signin") == ""
    code = code_for("x@y.in")
    assert portal.check_code("x@y.in", "signin", "000000" if code != "000000" else "111111").startswith("That code is not right")
    assert portal.check_code("x@y.in", "approve", code) == "Ask for a code first."                       # a sign-in code cannot approve
    assert portal.check_code("x@y.in", "signin", code[:3] + " " + code[3:]) == ""                        # spaces typed are fine
    assert portal.check_code("x@y.in", "signin", code) == "Ask for a code first."                        # used once
    portal.new_code("x@y.in", "signin")
    for _ in range(portal.CODE_TRIES):
        err = portal.check_code("x@y.in", "signin", "bad")
    assert err == "That code is not right. Ask for a new one."                                         # five tries, then dead
    portal.new_code("x@y.in", "signin")
    with db.Session() as s:                                                                            # ten minutes, then expired
        pc = s.query(db.PortalCode).order_by(db.PortalCode.id.desc()).first(); pc.expires = db.now() - datetime.timedelta(seconds=1); s.commit()
    assert portal.check_code("x@y.in", "signin", code_for("x@y.in")) == "This code has expired. Ask for a new one."
    assert portal.new_code("x@y.in", "signin") == "" and portal.new_code("x@y.in", "signin") == ""
    assert "Too many codes" in portal.new_code("x@y.in", "signin")                                     # five codes an hour
    with db.Session() as s:
        assert all(len(pc.code_hash) == 64 and not pc.code_hash.isdigit() for pc in s.query(db.PortalCode))   # only fingerprints kept


def test_portal(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    # the desk side: issue two drawings, give access, email them
    staff.post("/login/dev/2"); staff.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        staff.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    staff.post(A + "/draw")
    staff.post(A + "/issue", data={"folder_no": "6", "nos": "1,3", "back": "issue"})
    staff.post(A + "/access", data={"email": SINGH, "name": "Mr Singh", "can": "approve"})
    staff.post(A + "/access", data={"email": ACCOUNTS, "can": "view"})
    with db.Session() as s:
        revs = {r.number: r for r in s.query(db.Revision)}
    r1, r2 = revs["Encore-Nov-2026-06-01"], revs["Encore-Nov-2026-06-02"]

    # signed out: everything goes to the portal sign-in, never to the desk's
    c = TestClient(app)
    for u in ("/portal/orders", P, "%s/d/%d" % (P, r1.id), "%s/d/%d/pdf" % (P, r1.id), "/portal/past", P + "/set.pdf"):
        assert c.get(u, follow_redirects=False).headers["location"] == "/portal/login"
    assert "Send code" in c.get("/portal/login").text
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"

    # an email not on any list: the same answer, and no code is sent
    n = len(MAIL)
    v = c.post("/portal/code", data={"email": "stranger@gmail.com"}).text
    assert "if this email has access" in v and len(MAIL) == n
    assert "not right" in c.post("/portal/signin", data={"email": "stranger@gmail.com", "code": "123456"}).text
    assert "Enter your work email" in c.post("/portal/code", data={"email": ""}).text                     # empty value

    # the desk's drawings email: each listed recipient alone with their own link; CC gets a copy that signs nobody in
    MAIL.clear()
    staff.post(A + "/email", data={"kind": "first", "to": "%s, buyer@achievers.in" % SINGH, "cc": "kushal@encoreprimefurniture.com",
                                   "subject": "Drawings for approval", "body": "Dear Mr Singh", "attach": "1"})
    assert [(m["to"], m["cc"]) for m in MAIL] == [([SINGH], []), (["buyer@achievers.in"], ["kushal@encoreprimefurniture.com"])]
    link = re.search(r"(http\S+/portal/link/\S+)", MAIL[0]["body"]).group(1)
    assert "/portal/link/" not in MAIL[1]["body"] and "/portal" in MAIL[1]["body"] and all(m["att"] for m in MAIL)
    with db.Session() as s:
        assert s.query(db.Step).filter_by(kind="sent").one().note == "Emailed to %s, buyer@achievers.in" % SINGH

    # opening the link signs Mr Singh in directly, on that order
    r = c.get(link.split("testserver")[-1], follow_redirects=False)
    assert r.headers["location"] == P and portal.COOKIE in r.cookies
    assert c.get("/portal/link/not-a-real-token", follow_redirects=False).headers["location"] == "/portal/login?error=link"
    v = c.get(P).text
    assert "Drawings for your approval" in v and "Waiting</b>" not in v and "Encore-Nov-2026-06-01 Rev A" in v and "Approve 2 drawings" in v
    assert "Achiever Apparels Pvt Ltd" in v
    # only issued drawings: product 2 (a preview, not issued) is not there; an AI change waiting on product 1 changes nothing
    with db.Session() as s:
        l1 = s.query(db.OrderLine).filter_by(no=1).one()
        s.add(db.AiTurn(line_id=l1.id, typed="make it 1500", kind="change", status="trial", said="Length 1500", files={})); s.commit()
    v = c.get(P).text
    assert v.count('class="card" style="padding:12px"') == 2 and "1500" not in v
    assert c.get("%s/d/%d/sheet/1" % (P, r1.id)).headers["content-type"] == "image/png"
    assert c.get("%s/d/%d/pdf" % (P, r1.id)).content.startswith(b"%PDF")
    assert c.get(P + "/set.pdf").content.startswith(b"%PDF")
    with db.Session() as s:
        assert s.query(db.FileLog).filter_by(side="portal", who=SINGH).count() == 3
        d_id = s.query(db.Drawing).filter_by(line_id=s.query(db.OrderLine).filter_by(no=2).one().id).one().id
    assert c.get("%s/d/%d" % (P, 10 ** 6)).status_code == 404                                         # a drawing that is not issued here
    assert c.get("/drawings/%d/pdf" % d_id, follow_redirects=False).headers["location"] == "/login"     # the portal cookie is not a desk sign-in
    assert c.get("/portal/orders/SAMPLE-REPEAT-1").status_code == 404                                  # not on its list
    assert c.get("/portal/orders/NO-SUCH-ORDER").status_code == 404

    # home
    v = c.get("/portal/orders").text
    assert "Your orders" in v and "Q-2026-10-444" in v and "Review drawings" in v and "2 waiting for you" in v

    # a view-and-comment email: signs in with a code, asks a change with numbered marks; cannot approve
    b = TestClient(app)
    sign_in(b, ACCOUNTS, keep=False)
    assert portal.COOKIE in b.cookies
    v = b.get("%s/d/%d" % (P, r2.id)).text
    assert "can view and comment" in v and "Confirm approval" not in v
    assert "can view and comment" in b.post(P + "/approve", data={"do": "code"}).text
    assert "Say what you would like changed" in b.post("%s/d/%d/changes" % (P, r2.id), data={"marks": "[]", "note": ""}).text   # empty
    r = b.post("%s/d/%d/changes" % (P, r2.id), data={"marks": json.dumps([{"x": 40.5, "y": 20, "sheet": 1, "text": "Screen 800 wide please"},
                                                                       {"x": 500, "y": 1, "text": "off the sheet"}]), "note": "Darker top", "name": "Ms Rao"},
               files={"file": ("photo.jpg", b"\xff\xd8 photo", "image/jpeg")}, follow_redirects=False)
    assert r.headers["location"] == "%s/d/%d?done=changes" % (P, r2.id)
    with db.Session() as s:
        ap = s.query(db.Approval).filter_by(revision_id=r2.id).one()
        assert (ap.outcome, ap.how, ap.email, ap.name) == ("changes", "portal", ACCOUNTS, "Ms Rao") and "[1] Screen 800 wide please; Darker top" == ap.note
        assert open(ap.proof, "rb").read() == b"\xff\xd8 photo"
        mk = s.query(db.Mark).filter(db.Mark.by.like("Client:%")).one()
        assert (mk.shown, mk.x, mk.label) == ("r%d" % r2.id, 40.5, "Screen 800 wide please")
    assert any(m["subject"].startswith("Changes asked on the portal") and issuing.COMPANY in m["to"] for m in MAIL)
    h = staff.get(A + "/work").text                                         # issue #34: in the Client approvals column, not the conversation
    assert "Changes asked on the portal by Ms Rao" not in h and "Client approvals" in h and "Screen 800 wide please" in h
    assert "Changes asked" in c.get(P).text

    # approve the remaining (waiting) drawing: code, name, tick; each mistake said plainly
    MAIL.clear()
    v = c.post(P + "/approve", data={"do": "code", "name": "Mr Singh", "designation": "Admin head"}).text
    assert "We emailed a 6-digit code to %s" % SINGH in v and 'value="Admin head"' in v
    code = code_for(SINGH)
    assert "Tick the box" in c.post(P + "/approve", data={"do": "approve", "name": "Mr Singh", "code": code}).text
    assert "Enter your name" in c.post(P + "/approve", data={"do": "approve", "name": "", "code": code, "agree": "1"}).text
    assert "not right" in c.post(P + "/approve", data={"do": "approve", "name": "Mr Singh", "code": "000001" if code != "000001" else "000002", "agree": "1"}).text
    r = c.post(P + "/approve", data={"do": "approve", "name": "Mr Singh", "designation": "Admin head", "code": code, "agree": "1"}, follow_redirects=False)
    assert r.headers["location"] == P + "?done=approved-1"
    with db.Session() as s:
        ap = s.query(db.Approval).filter_by(revision_id=r1.id).one()
        assert (ap.outcome, ap.how, ap.code_verified, ap.email, ap.designation) == ("approved", "portal", True, SINGH, "Admin head")
        assert ap.pdf_sha256 == issuing.fingerprint(r1.files["pdf"]) and ap.ip and ap.on == datetime.date.today().isoformat()
        assert s.query(db.Step).filter_by(kind="approved").count() == 0                         # product 3 still has changes asked
        assert s.query(db.FileLog).filter_by(side="portal", action="approve").count() == 1
    conf = next(m for m in MAIL if m["subject"].startswith("Drawings approved"))
    assert conf["to"] == [SINGH] and conf["att"] and os.path.exists(conf["att"]) and "Encore-Nov-2026-06-01 Rev A" in conf["body"]
    assert "Approved by Mr Singh" in staff.get(A + "/issue").text

    # the approved drawing is locked: no change can be asked on it, here or in the desk
    v = c.get("%s/d/%d" % (P, r1.id)).text
    assert "This drawing is locked for manufacturing" in v and "Send change request" not in v
    assert "approved and locked" in c.post("%s/d/%d/changes" % (P, r1.id), data={"note": "change it"}).text
    assert "so it is locked" in staff.get(A + "/view/1").text
    assert "nothing waiting" in c.post(P + "/approve", data={"do": "code", "rid": str(r1.id)}).text

    # the one with changes asked cannot be approved: it comes back as a new revision first
    v = c.get("%s/d/%d" % (P, r2.id)).text
    assert "will come back to you as a new revision" in v and "Confirm approval" not in v and "Send change request" in v
    assert "nothing waiting" in c.post(P + "/approve", data={"do": "code", "rid": str(r2.id)}).text

def test_old_data_and_past_orders(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c = TestClient(app)
    with db.Session() as s:
        revs = {r.number: r for r in s.query(db.Revision)}
        r1, r2 = revs["Encore-Nov-2026-06-01"], revs["Encore-Nov-2026-06-02"]
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        # old data: an approval recorded by staff before the portal existed (by email) counts as approved
        s.add(db.Approval(order_id=o.id, revision_id=r2.id, outcome="approved", name="Mr Singh", how="email", on="2026-11-20", by="Sushil Singhal"))
        s.commit()
    staff.post("/login/dev/2")
    staff.post(A + "/answer-from-client/%d" % r2.id, data={"outcome": "approved", "name": "Mr Singh", "how": "phone", "note": "Called"})
    with db.Session() as s:
        assert s.query(db.Step).filter_by(kind="approved").one().note == "All 2 drawings approved (recorded on the Issue screen)"
    # old previews: an issued revision made before per-sheet previews existed gets them when first shown
    for f in os.listdir(os.path.dirname(r1.files["pdf"])):
        if ".sheet" in f:
            os.remove(os.path.join(os.path.dirname(r1.files["pdf"]), f))
    sign_in(c, SINGH)
    assert c.get("%s/d/%d/sheet/1" % (P, r1.id)).status_code == 200
    assert c.get("%s/d/%d/sheet/99" % (P, r1.id)).status_code == 404
    v = c.get(P).text
    assert "Approve remaining drawings" not in v and v.count(">Approved</span>") == 3

    # past orders: completed orders, with reorder to the sales person
    staff.post(A + "/step/released", data={"note": ""}); staff.post(A + "/step/completed", data={"note": ""})
    v = c.get("/portal/orders").text
    assert "No active orders" in v and "Ask to reorder" in v
    v = c.get("/portal/past").text
    assert "All drawings approved · final" in v and "Download final drawing set" in v
    assert "Tick at least one" in c.post(P + "/reorder", data={}, follow_redirects=True).text
    with db.Session() as s:
        l1 = s.query(db.OrderLine).filter_by(no=1).one().no
    MAIL.clear()
    r = c.post(P + "/reorder", data={"no": str(l1), "qty_%d" % l1: "5", "site": "Noida office", "note": "Same finish"}, follow_redirects=False)
    assert r.headers["location"] == "/portal/past?o=Q-2026-10-444&sent=1"
    m = MAIL[-1]
    assert m["subject"].startswith("Reorder asked") and "Product 1:" in m["body"] and "wants: 5" in m["body"] and "Noida office" in m["body"]
    assert "Reorder asked on the portal by %s" % SINGH in staff.get(A + "/work").text
    assert "No past orders found" in c.get("/portal/past?year=1999").text

    # access removed: signed out at once; the old email link no longer works
    with db.Session() as s:
        aid = s.query(db.PortalAccess).filter_by(email=SINGH).one().id
    staff.post("%s/access/%d/remove" % (A, aid))
    assert c.get("/portal/orders", follow_redirects=False).headers["location"] == "/portal/login"
    link = portal.link_for(SINGH, o.id, "http://testserver")
    assert c.get(link.split("testserver")[-1], follow_redirects=False).headers["location"] == "/portal/login?error=link"
    assert c.post("/portal/signout", follow_redirects=False).headers["location"] == "/portal/login"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
