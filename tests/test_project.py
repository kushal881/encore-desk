"""The project page, numbered markers on a drawing, and the dashboard numbers that open their drawings. Needs ENGINES_DIR."""
import os, sys, re, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def test_project():
    c.post("/login/dev/2")
    h = c.get(A).text                                                          # the project page comes first
    assert "WHAT TO DO NEXT" in h and "Answer 4 questions" in h and "4 questions to answer before drawing" in h and "Conversation</div>" not in h
    assert 'aria-current="step"><span class="dot">1</span>Questions' in h and "Remove from the desk" not in h      # Sushil is not the administrator
    assert "Back to the previous page" in h and "Back to the previous page" not in c.get("/").text
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        r = c.post("%s/answer/%d" % (A, qs[key]), data={"value": val}, follow_redirects=False)
        assert r.headers["location"] == A + "/work"
    h = c.get(A).text
    assert "Product 1, 2, 3 ready to draw" in h and "Draw the previews" in h and 'action="/orders/Q-2026-10-444/draw"' in h
    c.post(A + "/draw")
    h = c.get(A).text
    assert "Product 1, 2, 3 has a preview that is not issued" in h and "View, mark and ask for a change" in h and "Check the previews, then issue" in h
    w = c.get(A + "/work").text
    assert 'id="issue"' in w and "Product 1: new drawing number given after you enter the folder number" in w and "data-confirm=" in w
    # markers on the drawing
    v = c.get(A + "/view/2").text
    assert "+ Add numbered marker" in v and 'id="zin"' in v and 'id="zout"' in v and "None on this sheet" in v
    shown = v.split('name="shown" value="')[1].split('"')[0]
    assert c.post(A + "/marks/2", data={"shown": shown, "x": "140", "y": "10"}).status_code == 400
    c.post(A + "/marks/2", data={"shown": shown, "x": "31.5", "y": "42", "label": "left shutter"})
    v = c.post(A + "/marks/2", data={"shown": shown, "x": "60", "y": "20", "label": ""}).text
    assert v.count('class="pin" style="left:') == 2 and "left shutter" in v and "Change this drawing" in v and '<option value="1">1</option>' in v
    with db.Session() as s:
        m2 = s.query(db.Mark).filter_by(no=2).one().id
    c.post("%s/marks/2/%d/label" % (A, m2), data={"label": "top"})
    h = c.post(A + "/change", data={"text": "At 1 and 2 make it 1500 long", "line": "2", "shown": shown}).text
    assert re.search(r"At 1 and 2 make it 1500 long \[Markers: 1 = left shutter[^;\]]*; 2 = top[^\]]*\]", h) and "length 1500 mm" in h and "see the markers on the drawing" in h
    v = c.post("%s/marks/2/%d/remove" % (A, m2)).text
    assert v.count('class="pin" style="left:') == 1
    with db.Session() as s:
        cid = s.query(db.Change).filter_by(status="asked").one().id
    assert "1 change request waiting for your yes or no" in c.get(A).text
    c.post("%s/change/%d/yes" % (A, cid))
    v = c.get(A + "/view/2").text                                              # a new preview: the old markers stay with the old sheet
    assert v.count('class="pin" style="left:') == 0
    c.post(A + "/marks/2", data={"shown": v.split('name="shown" value="')[1].split('"')[0], "x": "5", "y": "5"})
    with db.Session() as s:
        assert sorted(m.no for m in s.query(db.Mark)) == [1, 2]                # numbers run on from the highest one in use
    # dashboard numbers open their drawings
    d = c.get("/").text
    assert 'href="/?focus=pending#drawings"' in d and "Drawings pending (" not in d
    d = c.get("/?focus=pending").text
    assert "Drawings pending (3)" in d and d.count(A + "/view/") >= 3 and "Preview, not issued" in d
    c.post(A + "/issue", data={"folder_no": "6"})
    assert "Drawings pending (0)" in c.get("/?focus=pending").text
    d = c.get("/?focus=with_client").text
    assert "Drawings waiting for client approval (3)" in d and "Encore-Nov-2026-06-02 Rev A" in d
    # a step taken on the project page comes back to the project page
    r = c.post(A + "/step/sent", data={"note": "Email"}, headers={"referer": "http://testserver" + A}, follow_redirects=False)
    assert r.headers["location"] == A
    h = c.get(A).text
    assert "Waiting for the client&#39;s approval" in h and "Record client approval" in h and "Nothing else is waiting on the drawings" in h
    d = c.get("/").text
    assert "What to do next" in d and "Waiting for the client&#39;s approval &rsaquo;" in d


def test_remove():
    """Only the administrator removes a project, and only while nothing is issued."""
    B = "/orders/SAMPLE-REPEAT-1"
    c.get(B)
    assert c.post(B + "/remove").status_code == 403
    c.post("/logout"); c.post("/login/dev/1")
    assert "Remove from the desk" in c.get(B).text and "Remove from the desk" not in c.get(A).text
    assert c.post(A + "/remove").status_code == 400                          # it has issued drawings
    r = c.post(B + "/remove", follow_redirects=False)
    assert r.headers["location"] == "/"
    with db.Session() as s:
        assert [o.source_ref for o in s.query(db.Order)] == ["Q-2026-10-444"]
    assert c.get(B).status_code == 200                                       # it can be opened again
