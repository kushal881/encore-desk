"""Step 7: the private storage bucket and signed links (storage.py). The bucket is replaced by a stand-in that keeps the
objects in a temporary folder; the signed-link format is checked with the real S3 library (signing needs no network).
The drawing part needs ENGINES_DIR."""
import os, sys, tempfile, shutil
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
for k in ("BUCKET", "AWS_S3_BUCKET_NAME", "ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from urllib.parse import unquote
from fastapi.testclient import TestClient
from desk.app import app
from desk import app as desk_app, db, engines, storage

A = "/orders/Q-2026-10-444"
c = TestClient(app)


class FakeBucket:
    """Stands in for the S3 client: objects in a folder, signed links that say what they were signed for."""
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.signed = []

    def _p(self, key):
        return os.path.join(self.dir, key)

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        os.makedirs(os.path.dirname(self._p(Key)), exist_ok=True)
        shutil.copy(Filename, self._p(Key))

    def download_file(self, Bucket, Key, Filename):
        shutil.copy(self._p(Key), Filename)

    def copy_object(self, Bucket, Key, CopySource):
        os.makedirs(os.path.dirname(self._p(Key)), exist_ok=True)
        shutil.copy(self._p(CopySource["Key"]), self._p(Key))

    def delete_object(self, Bucket, Key):
        os.remove(self._p(Key))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.signed.append((Params["Key"], ExpiresIn, Params["ResponseContentDisposition"]))
        return "https://bucket.example/%s?X-Amz-Expires=%d" % (Params["Key"], ExpiresIn)

    def keys(self):
        return sorted(os.path.relpath(os.path.join(r, f), self.dir) for r, _, fs in os.walk(self.dir) for f in fs)


def _pdf(path, pages=2):
    import pymupdf
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), "sheet %d" % (i + 1))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.save(path)
    return path


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket()
    monkeypatch.setattr(storage, "_CLIENT", fake)
    monkeypatch.setenv("BUCKET", "encore-files")
    return fake


def test_names_and_signed_links(monkeypatch):
    assert storage.client_folder("Encore-Nov-2026-06-02", "Achievers Apparels Pvt. Ltd.") == "Encore-Nov-2026-06-Achievers Apparels Pvt. Ltd."
    assert storage.client_folder("", "X") is None and storage.client_folder(None, "X") is None          # nothing issued yet
    assert storage.safe("A/B: C*") == "AB C" and storage.safe("") == "Client"
    k = storage.revision_keys("F", "Encore-Nov-2026-06-02", "B", 2)
    assert k == dict(pdf="F/PDF/Encore-Nov-2026-06-02-RevB.pdf", dxf="F/DXF/Encore-Nov-2026-06-02-RevB.dxf",
                     previews=["F/_previews/Encore-Nov-2026-06-02-RevB-1.png", "F/_previews/Encore-Nov-2026-06-02-RevB-2.png"])
    assert storage.old_key("F/PDF/x.pdf") == "F/Old Revisions/x.pdf" and storage.old_key("F/Other/a.pdf") == "F/Other/a.pdf"
    assert not storage.configured()
    # the real S3 library: a link to one object, valid for SIGNED_LINK_MINUTES only, private bucket by its own address
    monkeypatch.setattr(storage, "_CLIENT", None)
    for k_, v in dict(BUCKET="encore-files", ACCESS_KEY_ID="test-id", SECRET_ACCESS_KEY="test-secret", ENDPOINT="https://storage.example.com",
                      REGION="auto", SIGNED_LINK_MINUTES="3").items():
        monkeypatch.setenv(k_, v)
    assert storage.configured() and storage.minutes() == 3
    url = storage.signed("Encore-Nov-2026-06-X/PDF/Encore-Nov-2026-06-01-RevA.pdf", "Encore-Nov-2026-06-01-RevA.pdf", download=True)
    assert url.startswith("https://encore-files.storage.example.com/Encore-Nov-2026-06-X/PDF/") and "X-Amz-Expires=180" in url
    assert "X-Amz-Signature=" in url and "attachment" in url
    monkeypatch.setenv("SIGNED_LINK_MINUTES", "not a number")
    assert storage.minutes() == 5
    monkeypatch.setattr(storage, "_CLIENT", None)


def test_empty_and_missing_values(bucket):
    assert storage.keep(None, "k") == "" and storage.keep("/nowhere/x.pdf", "k") == "" and storage.key_of("") == ""
    assert storage.local(None) is None and storage.local("/nowhere/x.pdf") is None
    assert storage.keep_revision("F", "N", "A", {}) == 0 and storage.keep_revision(None, "N", "A", {"pdf": "/x.pdf"}) == 0
    assert storage.preview_paths("") == []
    with pytest.raises(Exception) as e:
        storage.serve(None, "/nowhere/x.pdf")
    assert getattr(e.value, "status_code", 0) == 404


def test_new_revision_moves_the_old_one(bucket):
    d = tempfile.mkdtemp()
    a = dict(pdf=_pdf(os.path.join(d, "a", "drawing.pdf")))
    b = dict(pdf=_pdf(os.path.join(d, "b", "drawing.pdf"), pages=1))
    assert storage.keep_revision("Encore-Nov-2026-06-C", "Encore-Nov-2026-06-01", "A", a) == 3            # pdf + 2 sheet previews
    assert storage.keep_revision("Encore-Nov-2026-06-C", "Encore-Nov-2026-06-01", "B", b, earlier=[a]) == 2
    assert bucket.keys() == ["Encore-Nov-2026-06-C/Old Revisions/Encore-Nov-2026-06-01-RevA.pdf",
                             "Encore-Nov-2026-06-C/PDF/Encore-Nov-2026-06-01-RevB.pdf",
                             "Encore-Nov-2026-06-C/_previews/Encore-Nov-2026-06-01-RevA-1.png",
                             "Encore-Nov-2026-06-C/_previews/Encore-Nov-2026-06-01-RevA-2.png",
                             "Encore-Nov-2026-06-C/_previews/Encore-Nov-2026-06-01-RevB-1.png"]
    assert storage.key_of(a["pdf"]) == "Encore-Nov-2026-06-C/Old Revisions/Encore-Nov-2026-06-01-RevA.pdf"
    # the disk copy lost (redeploy without a volume): fetched back from the bucket
    os.remove(b["pdf"])
    assert storage.local(b["pdf"]) == b["pdf"] and os.path.exists(b["pdf"])
    # the same file kept twice: one copy
    assert storage.keep(b["pdf"], storage.key_of(b["pdf"])) and len(bucket.keys()) == 5


def test_desk_files_through_the_bucket(monkeypatch):
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    assert c.get("/storage", follow_redirects=False).headers["location"] == "/login"          # signed out
    c.post("/login/dev/2"); c.get(A)                                                     # a drawing user
    assert c.get("/storage").status_code == 403
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")

    # old data: drawings issued before the bucket was set up are served from the disk, and logged
    c.post(A + "/issue", data={"folder_no": "6", "nos": "1", "back": "issue"})
    with db.Session() as s:
        r1 = s.query(db.Revision).one()
    r = c.get("/revisions/%d/pdf" % r1.id, follow_redirects=False)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    with db.Session() as s:
        g = s.query(db.FileLog).one()
        assert (g.who, g.side, g.action, g.key, g.name) == ("Sushil Singhal", "desk", "view", "", "Encore-Nov-2026-06-01-RevA.pdf")

    fake = FakeBucket()
    monkeypatch.setattr(storage, "_CLIENT", fake)
    monkeypatch.setenv("BUCKET", "encore-files")
    c.post("/login/dev/1")                                                               # the administrator
    v = c.get("/storage").text
    assert "encore-files" in v and "Issued drawings not in the bucket yet: <b>1</b>" in v and "Sushil Singhal" in v

    # a drawing with an AI change still waiting: the trial is never put in the bucket
    trial = _pdf(os.path.join(engines.FILES, "trial", "t.pdf"))
    with db.Session() as s:
        l2 = s.query(db.OrderLine).filter_by(no=2).one()
        s.add(db.AiTurn(line_id=l2.id, typed="make it 1500", kind="change", status="trial", files={"pdf": trial})); s.commit()
    c.post("/storage/keep-all")
    assert "Copied" in c.get("/storage?done=4").text
    folder = "Encore-Nov-2026-06-Achiever Apparels Pvt Ltd"
    assert "%s/PDF/Encore-Nov-2026-06-01-RevA.pdf" % folder in fake.keys() and "%s/DXF/Encore-Nov-2026-06-01-RevA.dxf" % folder in fake.keys()
    assert any(k.startswith(folder + "/_previews/Encore-Nov-2026-06-01-RevA-") for k in fake.keys())
    assert storage.key_of(trial) == "" and not any("trial" in k for k in fake.keys())

    # now a signed link, never the bucket's own address; the DXF downloads
    r = c.get("/revisions/%d/pdf" % r1.id, follow_redirects=False)
    assert r.status_code == 302 and unquote(r.headers["location"]).startswith("https://bucket.example/%s/PDF/" % folder) and "X-Amz-Expires=300" in r.headers["location"]
    c.get("/revisions/%d/dxf" % r1.id, follow_redirects=False)
    assert fake.signed[-1][2].startswith("attachment")

    # issued with the bucket on: kept straight away
    assert c.post(A + "/issue", data={"folder_no": "6", "nos": "2", "back": "issue"}).status_code == 400      # its AI change is waiting
    c.post(A + "/issue", data={"folder_no": "6", "nos": "3", "back": "issue"})
    assert "%s/PDF/Encore-Nov-2026-06-02-RevA.pdf" % folder in fake.keys()

    # the disk copy gone: the desk fetches it back to join the set and to show a sheet
    os.remove(r1.files["pdf"])
    r = c.get(A + "/combined.pdf")
    import pymupdf
    assert r.status_code == 200 and len(pymupdf.open(stream=r.content, filetype="pdf")) == 2
    assert c.get("/revisions/%d/big" % r1.id).status_code == 200

    # an approved (locked) drawing: the proof is kept and opens through a signed link
    c.post("%s/answer-from-client/%d" % (A, r1.id), data={"outcome": "approved", "name": "Mr Singh", "how": "email"},
           files={"proof": ("ok.eml", b"Approved", "message/rfc822")})
    with db.Session() as s:
        ap = s.query(db.Approval).one()
    assert storage.key_of(ap.proof).startswith(folder + "/Other/Approvals/")
    assert unquote(c.get("/approvals/%d/proof" % ap.id, follow_redirects=False).headers["location"]).startswith("https://bucket.example/%s/Other/Approvals/" % folder)
    assert "so it is locked" in c.get(A + "/view/1").text

    # a revision with no files at all (empty value): nothing to serve, nothing kept, no error
    with db.Session() as s:
        s.add(db.Revision(line_id=l2.id, number="Encore-Nov-2026-06-99", rev="A", files={})); s.commit()
        empty = s.query(db.Revision).filter_by(number="Encore-Nov-2026-06-99").one().id
    assert c.get("/revisions/%d/pdf" % empty).status_code == 404
    c.post("/storage/keep-all")
    with db.Session() as s:
        assert s.query(db.FileLog).filter(db.FileLog.key != "").count() >= 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
