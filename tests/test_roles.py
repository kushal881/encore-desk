"""Who sees what (Kushal 28-Nov-2026): the administrator everything; designers and the sales coordinator everything but the
administrator's controls, production included; a salesperson everything but the administrator's controls and production
drawings; production only production. Checked on every address, so a hidden link cannot be typed in. Needs ENGINES_DIR."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, engines, auth

c = TestClient(app)
A = "/orders/Q-2026-10-444"


def test_rules():
    assert [auth.role_for_email(e) for e in ("design@encoreprimefurniture.com", "Production@x.com", "sales@x.com", "salesteam2@x.com", "info@x.com", "", None)] == \
        ["drawing", "production", "sales", "sales", None, None, None]
    sales, prod, des, coord, adm = (NS(role=r) for r in ("sales", "production", "drawing", "coordinator", "admin"))
    for path, q in (("/orders/Q-1/production/2", ""), ("/production", ""), ("/sets/4/0/page/1", ""), ("/production-files/3", ""), ("/prodpreview/1/pdf", ""),
                    ("/prodchange/2/page/0", ""), ("/orders/Q-1/sets/production", ""), ("/orders/Q-1/bom", ""), ("/orders/Q-1/view/2/production", ""),
                    ("/orders/Q-1/view/2", "mode=prod&s=1")):
        assert not auth.allowed(sales, path, q) and auth.allowed(prod, path, q) and auth.allowed(des, path, q) and auth.allowed(coord, path, q) and auth.allowed(adm, path, q)
    for path in ("/orders/Q-1", "/orders/Q-1/work", "/orders/Q-1/view/2", "/projects", "/new", "/orders/Q-1/issue", "/orders/Q-1/changes", "/approvals", "/my-requests"):
        assert auth.allowed(sales, path) and not auth.allowed(prod, path)
    for path in ("/", "/tasks/5/done", "/photos/3"):
        assert auth.allowed(prod, path)
    assert auth.sees_production(des) and auth.sees_production(coord) and not auth.sees_production(sales)
    assert auth.sees_client(sales) and not auth.sees_client(prod)


def as_(email, role):
    with db.Session() as s:
        u = s.query(db.User).filter_by(email=email).first() or db.User(email=email, name=email.split("@")[0].title(), role=role)
        s.add(u); s.commit(); uid = u.id
    c.post("/logout"); c.post("/login/dev/%d" % uid)


def test_pages():
    if not engines.ENGINES_DIR:
        pytest.skip("ENGINES_DIR not set")
    c.post("/login/dev/2"); c.get(A)
    with db.Session() as s:
        qs = {q.key: q.id for q in s.query(db.Question)}
    for key, val in (("note", "Black bracket"), ("size", "L1200 D450 H750"), ("shelves", "1"), ("hinge", "Right")):
        c.post("%s/answer/%d" % (A, qs[key]), data={"value": val})
    c.post(A + "/draw"); c.post(A + "/issue", data={"folder_no": "5", "back": "issue"})
    with db.Session() as s:
        o = s.query(db.Order).one(); o.delivery_date = "10 DEC 2026"
        s.add(db.Step(order_id=o.id, kind="approved", note="ok", by="Sushil Singhal")); s.commit()
    c.post("/login/dev/1"); c.post(A + "/step/released", data={"note": ""}); c.post(A + "/sets/production", data={"job_order": "JO 1"})
    # sales: client side yes, production no (pages, files, viewer switch, project section)
    as_("sales@encoreprimefurniture.com", "sales")
    h = c.get(A).text
    assert c.get(A).status_code == 200 and "Open production sheet" not in h and "Production drawings" not in h and 'href="/production"' not in h
    assert "Production drawing</a>" not in c.get(A + "/view/1").text
    for url in (A + "/production/1", "/production", A + "/view/1?mode=prod", "/sets/1/0/page/0"):
        r = c.get(url)
        assert r.status_code == 403 and "not shown to sales" in r.text
    assert c.post(A + "/bom").status_code == 403 and c.post(A + "/sets/production").status_code == 403
    # production: production only; its home lists the products in production
    as_("production@encoreprimefurniture.com", "production")
    assert c.get("/", follow_redirects=False).headers["location"] == "/production"
    h = c.get("/production").text
    assert "Products in production (3)" in h and "P1 · not released yet" in h and 'href="/projects"' not in h
    assert c.get(A + "/production/1").status_code == 200
    for url in (A, A + "/work", A + "/view/1", "/projects", "/new", A + "/issue", "/approvals"):
        r = c.get(url)
        assert r.status_code == 403 and "Open Production" in r.text
    # designer and sales coordinator: everything but the administrator's controls, production included
    for email, role in (("design@encoreprimefurniture.com", "drawing"), ("info@encoreprimefurniture.com", "coordinator")):
        as_(email, role)
        assert c.get(A + "/production/1").status_code == 200 and c.get(A).status_code == 200 and "Open production sheet" in c.get(A).text
        assert 'href="/engine-requests" class="item' not in c.get("/").text                                           # admin controls stay admin's
        assert c.post("/approvals/engine/1/a").status_code in (403, 404) and c.post("/engines/refresh").status_code == 403
        assert c.post(A + "/remove").status_code == 403
    # old data: a user with a role from before this change (no role word) sees everything as before
    as_("old@encoreprimefurniture.com", "drawing")
    assert c.get(A + "/production/1").status_code == 200


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
