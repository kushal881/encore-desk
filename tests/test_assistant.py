"""The AI change panel: the assistant only proposes product-file values, the desk checks them, the engine draws a trial,
and nothing changes until Accept. Claude is replaced by a fixed reply here, so the tests cost nothing."""
import os, sys, tempfile, types, json
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, assistant

A = "/orders/Q-2026-10-444"
MODEL = {"type": "storage_shutters", "L": 1200, "D": 450, "H": 750, "t": 18, "shelves": 1, "hinge": "right", "screen": {"w": 900, "h": 300}}


# ---- what the assistant may change (no engine needed) ----------------------------------------------------------------
def test_check_keeps_to_the_product_file():
    ok, bad = assistant.check({"model.shelves": 2, "model.screen.w": 800, "model.hinge": "left"}, MODEL)
    assert ok == {"model.shelves": 2, "model.screen.w": 800, "model.hinge": "left"} and bad == []
    ok, bad = assistant.check({"model.L": 1500, "model.H": 7}, MODEL)
    assert ok == {"L": 1500} and bad == ["H must be a size in mm"]                       # the size goes with the order's values
    ok, bad = assistant.check({"model.shelves": "two", "model.glass_top": True, "swatches.0.code": "X", "model.screen.w": True}, MODEL)
    assert ok == {} and len(bad) == 4
    assert "glass_top is not a value this product has" in bad and "shelves must stay a number" in bad
    ok, _ = assistant.check({"model.interior": {"partitions": 1}, "model.raceway": True}, MODEL)    # engine options from the rules
    assert ok == {"model.interior": {"partitions": 1}, "model.raceway": True}
    sp = assistant.apply({"model": dict(MODEL, screen=dict(MODEL["screen"]))}, {"model.screen.w": 800, "model.interior": {"partitions": 1}, "L": 1500})
    assert sp["model"]["screen"] == {"w": 800, "h": 300} and sp["model"]["interior"] == {"partitions": 1} and sp["model"]["L"] == 1200


def test_without_a_key_the_desk_rules_stand_in():
    assert not assistant.configured()
    r = assistant.ask("make it 1500 long", {"family": "storage"}, MODEL, [], None)
    assert r["kind"] == "change" and r["values"] == {"L": 1500} and r["said"] == "length 1500 mm"
    r = assistant.ask("Why is the grommet on the left?", {"family": "workstation"}, MODEL, [], None)
    assert r["kind"] == "cannot" and "not switched on yet" in r["answer"]


def test_reply_is_checked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(assistant, "_call", lambda system, user: dict(kind="change", said="glass top", answer="",
                                                                       changes=[dict(path="model.glass_top", value="true")]))
    r = assistant.ask("add a glass top", {"family": "storage"}, MODEL, [], None)
    assert r["kind"] == "cannot" and "glass_top is not a value this product has" in r["answer"]


def test_the_request_sent_to_claude(monkeypatch):
    """One structured-output request: Claude Opus 5.5, refusal fallback on, the rules in a cached system prompt."""
    sent = {}
    class Msgs:
        def create(self, **kw):
            sent.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(
                dict(kind="answer", said="", changes=[], answer="ZEE rule: grommet 100 x 100 from the left corner.")))])
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=Msgs())))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    edir = tempfile.mkdtemp(); open(os.path.join(edir, "ENCORE_DRAWING_RULES.txt"), "w").write("RULE: grommet 100 x 100 from the left.")
    r = assistant.ask("Why is the grommet on the left?", {"family": "workstation"}, MODEL, ["1 = grommet, on Cable grommet (top view)"], edir)
    assert r == dict(kind="answer", said="", values={}, problems=[], answer="ZEE rule: grommet 100 x 100 from the left corner.")
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["format"]["schema"] == assistant.SCHEMA and sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "RULE: grommet 100 x 100" in sent["system"][0]["text"] and "1 = grommet, on Cable grommet (top view)" in sent["messages"][0]["content"]
    assert "never edit a PDF" in sent["system"][0]["text"]


# ---- the panel, with the engine --------------------------------------------------------------------------------------
c = TestClient(app)
REPLY = {}


def fake(system, user):
    return dict(REPLY)


def _setup(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(assistant, "_call", fake)


def _drawing():
    with db.Session() as s:
        return s.query(db.Drawing).filter_by(id=2).one()


def _turns():
    with db.Session() as s:
        return s.query(db.AiTurn).order_by(db.AiTurn.id).all()


def test_panel(monkeypatch):
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    _setup(monkeypatch)
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw")
    v = c.get(A + "/view/2").text
    assert "Change this drawing" in v and "Not issued yet · changes keep Rev A" in v
    before = _drawing()
    shelves = before.spec["model"]["shelves"]

    # a question gets an answer, nothing is drawn
    REPLY.update(kind="answer", said="", changes=[], answer="The shelves are fixed with minifix and dowels.")
    v = c.post(A + "/view/2/ai", data={"text": "How are the shelves fixed?", "marker": "0"}).text
    assert "How are the shelves fixed?" in v and "The shelves are fixed with minifix and dowels." in v and _turns()[-1].status == "answer"

    # a change: a trial is drawn and checked, the drawing itself stays as it was
    REPLY.update(kind="change", said="Shelves %d -> 2" % shelves, answer="", changes=[dict(path="model.shelves", value="2")])
    v = c.post(A + "/view/2/ai", data={"text": "two shelves please", "marker": "0"}).text
    t1 = _turns()[-1]
    assert t1.status == "trial" and t1.values == {"model.shelves": 2} and os.path.exists(t1.files["pdf"])
    assert all(x["ok"] for x in t1.files["checks"]) and t1.spec["model"]["shelves"] == 2
    assert _drawing().spec == before.spec and _drawing().files["pdf"] == before.files["pdf"]
    assert "Shelves %d -&gt; 2" % shelves in v and "/ai/%d/accept" % t1.id in v and "all automatic checks pass" in v
    assert c.get("/ai/%d/big?page=0" % t1.id).headers["content-type"] == "image/png"
    v = c.get(A + "/view/2?compare=%d" % t1.id).text
    assert "Compare: Shelves" in v and "Before · current" in v and "changed area outlined" in v

    # Undo drops a trial
    c.post(A + "/view/2/ai/%d/undo" % t1.id)
    assert _turns()[-1].status == "undone" and _drawing().spec == before.spec
    assert c.post(A + "/view/2/ai/%d/accept" % t1.id).status_code == 400

    # Accept: the trial becomes the drawing and the change is recorded
    c.post(A + "/view/2/ai", data={"text": "two shelves please", "marker": "0"})
    t2 = _turns()[-1]
    c.post(A + "/view/2/ai", data={"text": "two shelves please", "marker": "0"})
    t3 = _turns()[-1]
    c.post(A + "/view/2/ai/%d/accept" % t2.id)
    d = _drawing()
    assert d.spec["model"]["shelves"] == 2 and d.files["pdf"] == t2.files["pdf"] and d.status == "preview"
    with db.Session() as s:
        ch = s.query(db.Change).filter_by(status="confirmed").one()
        assert ch.values == {"model.shelves": 2} and ch.understood == "Shelves %d -> 2" % shelves
    assert c.post(A + "/view/2/ai/%d/accept" % t3.id).status_code == 400       # made on the drawing before: ask again
    w = c.get(A + "/work").text
    assert "Accepted by Sushil Singhal from the change panel" in w

    # a redraw keeps the accepted value (it is in the product file now)
    c.post(A + "/draw", data={"no": "2"})
    assert _drawing().spec["model"]["shelves"] == 2

    # Undo an accepted change that is not issued: drawn again without it
    c.post(A + "/view/2/ai/%d/undo" % t2.id)
    assert _drawing().spec["model"]["shelves"] == shelves and _turns()[t2.id - 1].status == "withdrawn"
    assert "Undone" in c.get(A + "/work").text

    # the engine refuses: the reason is shown and it cannot be accepted
    real = engines.draw
    def refuse(spec, key, edir=None):
        raise engines.EngineFailed("'shelf1' and 'partition' overlap (10 x 10 x 10 mm) - move one of them.", engines.checks_from("CLASH: 'shelf1' and 'partition' overlap", False))
    monkeypatch.setattr(engines, "draw", refuse)
    v = c.post(A + "/view/2/ai", data={"text": "two shelves please", "marker": "0"}).text
    monkeypatch.setattr(engines, "draw", real)
    t4 = _turns()[-1]
    assert t4.status == "failed" and "The engine refused it" in v and "/ai/%d/accept" % t4.id not in v
    assert c.post(A + "/view/2/ai/%d/accept" % t4.id).status_code == 400

    # a value the product does not have is refused before anything is drawn
    REPLY.update(kind="change", said="glass top", answer="", changes=[dict(path="model.glass_top", value="true")])
    v = c.post(A + "/view/2/ai", data={"text": "add a glass top", "marker": "0"}).text
    assert _turns()[-1].status == "cannot" and "glass_top is not a value this product has" in v

    # a marker is passed on with the request
    with db.Session() as s:
        key = "d%d" % int(_drawing().made_at.timestamp())
    c.post(A + "/marks/2", data={"shown": key, "x": "30", "y": "40", "label": "this shelf"})
    seen = {}
    monkeypatch.setattr(assistant, "_call", lambda system, user: seen.setdefault("user", user) and dict(kind="answer", said="", changes=[], answer="ok"))
    c.post(A + "/view/2/ai", data={"text": "is this fixed?", "marker": "1"})
    assert "At marker 1: is this fixed?" in seen["user"] and "1 = this shelf" in seen["user"]


def test_issued_then_approved(monkeypatch):
    if not engines.ENGINES_DIR:
        import pytest; pytest.skip("ENGINES_DIR not set")
    _setup(monkeypatch)
    assert "Accept or Undo the change first" in c.get(A + "/issue").text        # a proposal left waiting blocks issue
    for tr in [x for x in _turns() if x.status == "trial"]:
        c.post(A + "/view/2/ai/%d/undo" % tr.id)
    c.post(A + "/issue", data={"folder_no": "6"})
    v = c.get(A + "/view/2").text
    assert "Issued as Rev A · an accepted change makes a new revision when issued" in v
    REPLY.update(kind="change", said="Shelves -> 2", answer="", changes=[dict(path="model.shelves", value="2")])
    v = c.post(A + "/view/2/ai", data={"text": "two shelves", "marker": "0"}).text
    t = _turns()[-1]
    assert "Accepting changes it; issuing it again makes a new revision." in v
    c.post(A + "/view/2/ai/%d/accept" % t.id)
    h = c.post(A + "/issue").text
    with db.Session() as s:
        revs = [r.rev for r in s.query(db.Revision).join(db.OrderLine).filter(db.OrderLine.no == 2).order_by(db.Revision.id)]
        last = s.query(db.Revision).order_by(db.Revision.id.desc()).first()
    assert revs == ["A", "B"] and last.spec["model"]["shelves"] == 2 and last.spec["revisions"][-1]["desc"] == "shelves 2" and last.reason == "Shelves -> 2"
    assert c.post(A + "/view/2/ai/%d/undo" % t.id).status_code == 400            # issued: a further change is a new revision
    c.post(A + "/step/sent"); c.post(A + "/step/approved", data={"note": "Mr Singh, by email"})
    v = c.get(A + "/view/2").text
    assert "The client has approved this drawing, so it is locked." in v and 'action="/orders/Q-2026-10-444/view/2/ai"' not in v
    assert c.post(A + "/view/2/ai", data={"text": "two shelves", "marker": "0"}).status_code == 400
    assert "Change this drawing" not in c.get(A + "/view/2?mode=prod").text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
