"""Drawing page, chat style (issue #82): click the drawing to add a marker, it becomes a chip in the message box; several
chips plus typed text go out with ONE Send; the AI answers with one line per marker; one trial drawing for the message;
one pinned "N changes waiting · Accept all" bar; Undo per line; lines the engine cannot draw go to the bridge.
Covers old data (turns and markers from before), an AI change waiting, an approved / locked drawing, empty values and
40 markers on one sheet. The assistant is stood in for (no API key in tests). Needs ENGINES_DIR."""
import os, sys, tempfile, re
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app, load
from desk import db, engines, assistant

A = "/orders/Q-2026-10-444"
c = TestClient(app)
FETCH = {"x-requested-with": "fetch"}


def fake_ask(text, product, model, markers, edir, history=(), images=()):
    """shelf -> a change the engine draws; raceway -> the engine cannot; anything else -> an answer."""
    if "shelf" in text or "shelves" in text:
        return dict(kind="change", said="Shelves -> 2", values={"model.shelves": 2}, problems=[], answer="")
    if "length" in text:
        return dict(kind="change", said="Length 1200 -> 1300", values={"model.L": 1300}, problems=[], answer="")
    if "raceway" in text:
        return dict(kind="cannot", said="", values={}, problems=[], answer="The engine cannot draw a raceway on this product yet.")
    return dict(kind="answer", said="", values={}, problems=[], answer="It is fixed with minifix.")


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(assistant, "ask", fake_ask)


def key_of(v):
    return v.split('data-key="')[1].split('"')[0]


def line(no=2):
    return next(l for l in load("Q-2026-10-444").lines if l.no == no)


def turns():
    with db.Session() as s:
        return s.query(db.AiTurn).order_by(db.AiTurn.id).all()


def test_setup_and_old_data():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for k, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[k]), data={"value": val})
    c.post(A + "/draw")
    v = c.get(A + "/view/2").text
    key = key_of(v)
    # old data: an AI turn and a marker made by the earlier page (no parts, marker never "sent")
    with db.Session() as s:
        lid = line().id
        s.add(db.AiTurn(line_id=lid, shown=key, typed="how is the shelf fixed?", kind="answer", answer="With shelf pins.", status="answer", by="Sushil Singhal"))
        s.add(db.Mark(line_id=lid, shown=key, no=1, x=20, y=30, label="old note", by="Sushil Singhal"))
        s.commit()
    v = c.get(A + "/view/2").text
    assert "how is the shelf fixed?" in v and "With shelf pins." in v                          # old turn as chat history
    chips = v.split('id="chips"')[1].split("</div>")[0]
    assert "old note" in chips and 'data-mark="1"' in chips                                   # old marker: a chip not sent yet
    assert 'id="sendform"' in v and "0 changes waiting" not in v and 'id="waitbar"' not in v


def test_markers_send_one_message():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    v = c.get(A + "/view/2").text
    key = key_of(v)
    # click the drawing -> part lookup; Enter -> the chip, without reloading (JSON)
    assert c.get(A + "/view/2/part", params={"shown": key, "x": 50, "y": 50}).json().keys() == {"part"}
    m2 = c.post(A + "/marks/2", data={"shown": key, "x": "40", "y": "40", "label": "two shelves here"}, headers=FETCH).json()
    m3 = c.post(A + "/marks/2", data={"shown": key, "x": "60", "y": "40", "label": "raceway along the back"}, headers=FETCH).json()
    m4 = c.post(A + "/marks/2", data={"shown": key, "x": "70", "y": "60", "label": ""}, headers=FETCH).json()   # empty note
    assert m2["no"] == 2 and m3["no"] == 3 and m4["label"] != ""                                # empty: the part name (or the place)
    assert c.post(A + "/marks/2/%d/remove" % m4["id"], headers=FETCH).json() == {"ok": True}
    # empty send refused, with a friendly message
    with db.Session() as s:                                                                    # the old chip goes too: drop it first
        s.query(db.Mark).filter_by(no=1).delete(); s.commit()
    # one Send: two chips + typed text = ONE message, one line each, ONE trial drawing
    r = c.post(A + "/view/2/ai", data={"text": "and make the length 1300"})
    assert r.status_code == 200
    t = turns()[-1]
    assert t.status == "trial" and len(t.parts) == 3 and [p["marker"] for p in t.parts] == [2, 3, 0]
    assert [p["kind"] for p in t.parts] == ["change", "cannot", "change"]
    assert t.values == {"model.shelves": 2, "model.L": 1300} and t.spec["model"]["L"] == 1300 and os.path.exists(t.files["pdf"])
    with db.Session() as s:
        assert {m.no: m.turn_id for m in s.query(db.Mark)} == {2: t.id, 3: t.id}                # chips sent with this message
    v = r.text
    assert "2 changes waiting" in v and "Accept all" in v and "&rarr; Bridge" in v and "/work#bridge2" in v
    assert 'id="chips"' in v and "two shelves here</span><button" not in v                     # message box empty again
    # Undo one line: the trial is drawn again without it
    c.post(A + "/view/2/ai/%d/drop/2" % t.id)
    t = turns()[-1]
    assert t.status == "trial" and t.values == {"model.shelves": 2} and t.parts[2]["dropped"] and "L" not in str(t.values)
    v = c.get(A + "/view/2").text
    assert "1 change waiting" in v
    # empty send: refused, nothing made
    n = len(turns())
    r = c.post(A + "/view/2/ai", data={"text": "   "})
    assert r.status_code == 400 and "Add a marker on the drawing or type a change first" in r.text and len(turns()) == n


def test_accept_all():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    t = turns()[-1]
    c.post(A + "/view/2/ai/%d/accept" % t.id)
    l = line()
    assert l.drawing.spec["model"]["shelves"] == 2 and turns()[-1].status == "accepted"
    v = c.get(A + "/view/2").text
    assert "Accepted 1 change · preview redrawn" in v and 'id="waitbar"' not in v
    # a second message while nothing waits; then one while it waits carries it along
    c.post(A + "/view/2/ai", data={"text": "make the length 1300"})
    first = turns()[-1]
    c.post(A + "/view/2/ai", data={"text": "how is it fixed?"})
    last = turns()[-1]
    assert turns()[-2].status == "merged" and last.status == "trial" and last.values == {"model.L": 1300}
    assert "1 change waiting" in c.get(A + "/view/2").text
    c.post(A + "/view/2/ai/%d/undo" % last.id)                                                 # dropped: nothing waits
    assert first.id != last.id and "changes waiting" not in c.get(A + "/view/2").text


def test_forty_markers_one_screen():
    """40 markers on one sheet: all chips in the message box, which sits outside the scrolling message list."""
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    key = key_of(c.get(A + "/view/2").text)
    for i in range(40):
        c.post(A + "/marks/2", data={"shown": key, "x": str(2 + i * 2), "y": "50", "label": "note %d" % i}, headers=FETCH)
    v = c.get(A + "/view/2").text
    chips = v.split('id="chips"')[1].split("</div>")[0]
    assert chips.count('class="chip"') == 40 and v.count('class="pin" data-mark=') == 40
    msgs_at, form_at = v.index('id="msgs"'), v.index('id="sendform"')
    assert msgs_at < form_at and v.index("</section>", msgs_at) > form_at                   # the message box is pinned below the list
    assert "#msgs{flex:1 1 auto;min-height:0;overflow-y:auto" in v and "height:calc(100vh - 150px)" in v


def test_locked_drawing_has_no_message_box():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    with db.Session() as s:
        s.query(db.Mark).delete(); s.commit()
    c.post(A + "/issue", data={"folder_no": "6", "nos": "2", "back": "issue"})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        r = s.query(db.Revision).first()
        s.add(db.Approval(order_id=o.id, revision_id=r.id, outcome="approved", name="Mr Singh", how="email", on="28 NOV 2026")); s.commit()
    v = c.get(A + "/view/2").text
    assert "so it is locked" in v and 'id="sendform"' not in v and 'id="pop"' not in v
    assert 'action="/orders/Q-2026-10-444/view/2/permission"' in v                          # the permission request still works
    assert c.post(A + "/view/2/ai", data={"text": "two shelves"}).status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
