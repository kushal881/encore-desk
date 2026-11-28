"""Stop alerts by email (decision 12): administrators, secondary administrators, production users and the project's
salesperson get a task and an email; a failed email or a person without an address never stops the alert."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, alerts, issuing

c = TestClient(app)


def _order(s, salesperson):
    o = s.query(db.Order).first()
    o.salesperson = salesperson
    return o


def test_stop_alert(monkeypatch):
    c.post("/login/dev/2"); c.get("/orders/Q-2026-10-444")
    sent = []
    monkeypatch.setattr(issuing, "send", lambda to, cc, subject, body, *a: sent.append((to, subject, body)))
    with db.Session() as s:
        s.add(db.User(email="production@encoreprimefurniture.com", name="Production", role="production"))
        s.add(db.User(email="old.prod@encoreprimefurniture.com", name="Gone", role="production", active=False))   # switched off: never
        s.commit()
        o = _order(s, "Anchal Dawar")                                         # the salesperson is a desk user (info@)
        names = alerts.stop(s, o, 3, "STOP: test change", "quotestop:1", "https://portal.encoreprimefurniture.com")
        s.commit()
        tasks = {t.assignee for t in s.query(db.Task).filter_by(source="quotestop:1")}
        msg = s.query(db.Message).order_by(db.Message.id.desc()).first().text
    assert set(names) == tasks == {"Kushal Singhal", "Sushil Singhal", "Production", "Anchal Dawar"}
    assert sent and set(sent[0][0]) == {"kushal@encoreprimefurniture.com", "sushil@encoreprimefurniture.com", "production@encoreprimefurniture.com",
                                         "info@encoreprimefurniture.com"}
    assert sent[0][1].startswith("STOP: Q-2026-10-444") and "https://portal.encoreprimefurniture.com/orders/Q-2026-10-444" in sent[0][2]
    assert msg.startswith("Stop alert sent to") and "emailed to" in msg
    # a salesperson who is not a desk user (no address) and email not set up: the tasks still go out, history says so
    def fail(*a):
        raise RuntimeError("Email is not set up yet")
    monkeypatch.setattr(issuing, "send", fail)
    with db.Session() as s:
        o = _order(s, "Balvinder Singh")
        names = alerts.stop(s, o, 1, "STOP: second", "quotestop:2")
        s.commit()
        msg = s.query(db.Message).order_by(db.Message.id.desc()).first().text
    assert "Balvinder Singh" in names and "could not be sent (Email is not set up yet)" in msg and "no email address for Balvinder Singh" in msg
    # an order with no salesperson (old data): only the administrators and production
    with db.Session() as s:
        o = _order(s, "")
        assert {n for n, _ in alerts.recipients(s, o)} == {"Kushal Singhal", "Sushil Singhal", "Production"}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
