"""Issue (screen 6): issue the drawings picked, the combined PDF, the email to the client (sent from the company address,
PDF attached), the portal access list, and the client's answers per drawing with the approved file's fingerprint.
Sending mail is replaced by a stand-in. The drawing part needs ENGINES_DIR."""
import os, sys, tempfile, types
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
os.environ.pop("SMTP_PASSWORD", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, issuing

A = "/orders/Q-2026-10-444"


def test_templates_and_addresses():
    s, b = issuing.fill("first", "Q-1", "Achievers Apparels", "Mr Singh", ["Encore-Nov-2026-06-01 Rev A", "Encore-Nov-2026-06-02 Rev A"], "Suraj")
    assert s == "Drawings for approval: Q-1 · Achievers Apparels" and b.startswith("Dear Mr Singh,") and "(2 drawings, Encore-Nov-2026-06-01 Rev A, Encore" in b
    assert "reply to this email to approve" in b and b.endswith("Suraj\nEncore Prime Furniture")
    assert issuing.fill("revised", "Q-1", "X", "", ["A Rev B"], "K")[0] == "Revised drawings: Q-1 · X" and "Dear Sir / Madam" in issuing.fill("first", "Q", "X", "", [], "K")[1]
    assert issuing.emails("a@x.com, B@Y.in;c@z.co") == ["a@x.com", "b@y.in", "c@z.co"]
    with pytest.raises(ValueError):
        issuing.emails("a@x.com, not-an-email")
    assert issuing.combined_name("Achievers Apparels Pvt Ltd").startswith("Achievers Apparels Pvt Ltd-") and issuing.combined_name("A/B").startswith("AB-")


def test_send_builds_the_mail(monkeypatch):
    sent = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout): sent["server"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["msg"] = m
    monkeypatch.setattr(issuing.smtplib, "SMTP", FakeSMTP)
    with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
        issuing.send(["a@x.com"], [], "S", "B")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    f = os.path.join(tempfile.mkdtemp(), "set.pdf"); open(f, "wb").write(b"%PDF-1.4 test")
    issuing.send(["a@x.com"], ["b@x.com"], "Subject", "Body", f, "Client-27-Nov-2026.pdf")
    m = sent["msg"]
    assert sent["server"] == ("smtp.gmail.com", 587) and sent["tls"] and sent["login"] == ("info@encoreprimefurniture.com", "app-password")
    assert m["To"] == "a@x.com" and m["Cc"] == "b@x.com" and "info@encoreprimefurniture.com" in m["From"]
    assert [p.get_filename() for p in m.iter_attachments()] == ["Client-27-Nov-2026.pdf"]


c = TestClient(app)
MAIL = []


def _issued():
    with db.Session() as s:
        return sorted((r.number, r.rev) for r in s.query(db.Revision))


def test_issue_screen(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    v = c.get(A + "/issue").text
    assert "Ready to issue (3)" in v and "Folder number for" in v and "Issue and approval</a>" in v

    # only the drawings picked; none picked issues nothing
    assert c.post(A + "/issue", data={"folder_no": "6", "nos": "none", "back": "issue"}).status_code == 400
    r = c.post(A + "/issue", data={"folder_no": "6", "nos": "1,3", "back": "issue"}, follow_redirects=False)
    assert r.headers["location"] == A + "/issue" and _issued() == [("Encore-Nov-2026-06-01", "A"), ("Encore-Nov-2026-06-02", "A")]
    v = c.get(A + "/issue").text
    assert "Ready to issue (1)" in v and "Issued drawings (2)" in v
    r = c.get(A + "/combined.pdf")
    import pymupdf
    assert r.status_code == 200 and len(pymupdf.open(stream=r.content, filetype="pdf")) == 2      # both issued drawings, in order

    # portal access list: one email at a time, logged
    assert "is not an email address" in c.post(A + "/access", data={"email": "mr singh", "can": "approve"}).text
    c.post(A + "/access", data={"email": "Singh@Achievers.in", "name": "Mr Singh", "can": "approve"})
    c.post(A + "/access", data={"email": "accounts@achievers.in", "can": "view"})
    v = c.get(A + "/issue").text
    assert "singh@achievers.in · Mr Singh" in v and "Can approve" in v and "View &amp; comment" in v
    assert 'name="to" value="singh@achievers.in"' in v                                     # To = who can approve
    with db.Session() as s:
        aid = s.query(db.PortalAccess).filter_by(email="accounts@achievers.in").one().id
    c.post("%s/access/%d/remove" % (A, aid))
    w = c.get(A + "/work").text
    assert "Portal access given for singh@achievers.in (can approve) by Sushil Singhal" in w and "Portal access removed for accounts@achievers.in" in w

    # email: not set up -> said plainly, nothing marked as sent
    v = c.post(A + "/email", data={"kind": "first", "to": "singh@achievers.in", "subject": "Drawings", "body": "Dear Mr Singh", "attach": "1"}).text
    assert "Email is not set up yet: add SMTP_PASSWORD" in v and "not sent" in v
    with db.Session() as s:
        assert s.query(db.Step).filter_by(kind="sent").count() == 0
    # set up: sent with the combined PDF, the order is marked as sent
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, att=None, name=None: MAIL.append((to, cc, subject, att, name)))
    v = c.get(A + "/issue?t=additional").text
    assert "Additional drawings: Q-2026-10-444" in v
    c.post(A + "/email", data={"kind": "first", "to": "singh@achievers.in", "cc": "kushal@encoreprimefurniture.com", "subject": "Drawings for approval",
                               "body": "Dear Mr Singh", "attach": "1"})
    to, cc, subject, att, name = MAIL[-1]
    assert to == ["singh@achievers.in"] and cc == ["kushal@encoreprimefurniture.com"] and os.path.exists(att) and name.endswith(".pdf")
    with db.Session() as s:
        assert s.query(db.Step).filter_by(kind="sent").one().note == "Emailed to singh@achievers.in"

    # the client's answers, per drawing
    with db.Session() as s:
        revs = {r.number: r for r in s.query(db.Revision)}
    r1, r2 = revs["Encore-Nov-2026-06-01"], revs["Encore-Nov-2026-06-02"]
    assert "Enter who answered" in c.post("%s/answer-from-client/%d" % (A, r1.id), data={"outcome": "approved", "name": ""}).text
    c.post("%s/answer-from-client/%d" % (A, r2.id), data={"outcome": "changes", "name": "Mr Singh", "how": "phone", "note": "Make it 1500 long"})
    c.post("%s/answer-from-client/%d" % (A, r1.id), data={"outcome": "approved", "name": "Mr Singh", "designation": "Admin head", "how": "email", "on": "2026-11-27"},
           files={"proof": ("approval.eml", b"From: singh@achievers.in\n\nApproved.", "message/rfc822")})
    with db.Session() as s:
        ap = s.query(db.Approval).filter_by(revision_id=r1.id).one()
        assert ap.pdf_sha256 == issuing.fingerprint(r1.files["pdf"]) and ap.designation == "Admin head" and ap.by == "Sushil Singhal"
        assert s.query(db.Step).filter_by(kind="approved").count() == 0                         # one drawing still has changes asked
        aid = ap.id
    assert c.get("/approvals/%d/proof" % aid).content.startswith(b"From: singh@achievers.in")
    v = c.get(A + "/issue").text
    assert "Approved by Mr Singh · 2026-11-27" in v and "Changes asked" in v
    assert "The client approved Rev A on 2026-11-27 (Mr Singh), so it is locked." in c.get(A + "/view/1").text   # the change panel is off
    c.post("%s/answer-from-client/%d" % (A, r2.id), data={"outcome": "approved", "name": "Mr Singh", "how": "email"},
           files={"proof": ("ok.eml", b"Approved", "message/rfc822")})
    with db.Session() as s:
        assert s.query(db.Step).filter_by(kind="approved").one().note == "All 2 drawings approved (recorded on the Issue screen)"
    c.post("/logout")
    assert c.get(A + "/combined.pdf", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/approvals/%d/proof" % aid, follow_redirects=False).headers["location"] == "/login"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
