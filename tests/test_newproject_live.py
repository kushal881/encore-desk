"""New project: quotation results as you type, click to select (issue #86).
Covers a quotation already on the desk (Open, never a duplicate), Odoo not reachable (a friendly message, no error page),
1 letter typed (no search), no matches, a quotation with 100 lines (counted, not listed; per area) with bought-in,
service and delivery lines counted as no drawing (hard rule 6), the brief for the designer, and the dashboard box
(a client name goes to the results, not a "not found" page)."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from desk.app import app
from desk import db, newproject as np
from desk.nodrawing import classify
from desk.sources.base import SourceOrder, SourceLine
from desk.sources.odoo import OdooError

c = TestClient(app)
c.post("/login/dev/2")


def test_one_letter_and_no_matches():
    assert c.get("/new/search?q=a").json() == dict(q="a", results=[], note="", min=2)          # 1 letter: nothing searched
    assert c.get("/new/search?q=").json()["results"] == []
    assert c.get("/new/search?q=zzqq").json()["results"] == []
    assert "No quotation or client matches" in c.get("/new?q=zzqq").text
    assert "Type at least 2 letters" in c.get("/new?q=a").text


def test_results_and_already_on_desk():
    r = c.get("/new/search?q=achiev").json()["results"]                                    # by client name, any case
    q = next(x for x in r if x["ref"] == "Q-2026-10-444")
    assert q["client"] == "Achiever Apparels Pvt Ltd" and q["lines"] >= 3 and not q["on_desk"] and all("achiev" in x["client"].lower() for x in r)
    h = c.get("/new?ref=Q-2026-10-444").text
    assert "Selected" in h and "Change" in h and "we draw" in h and 'name="ref" value="Q-2026-10-444"' in h
    assert '<button class="primary big" id="createbtn" >' in h                                    # Create project is on
    assert c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": "5", "notes": "Site at Gurugram, 3rd floor", "needed_by": "2026-12-05",
                                "designer": "Neha Sharma"}, follow_redirects=False).headers["location"] == "/orders/Q-2026-10-444"
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="Q-2026-10-444").one()
        assert (o.brief, o.needed_by, o.designer) == ("Site at Gurugram, 3rd floor", "2026-12-05", "Neha Sharma")
        assert any(m.text.startswith("Brief for the designer") and "Needed by 2026-12-05" in m.text for m in o.messages)
    r = c.get("/new/search?q=Q-2026").json()["results"]
    assert next(x for x in r if x["ref"] == "Q-2026-10-444")["on_desk"]
    assert "Already on the desk" in c.get("/new/pick?ref=Q-2026-10-444").text and "we draw" not in c.get("/new/pick?ref=Q-2026-10-444").text
    # selecting it again opens the project, no duplicate
    c.post("/new", data={"ref": "Q-2026-10-444", "folder_no": ""})
    with db.Session() as s:
        assert s.query(db.Order).filter_by(source_ref="Q-2026-10-444").count() == 1
    # old data: a project made before the brief existed (no brief given) has empty brief fields and no brief message
    c.post("/new", data={"ref": "SAMPLE-REPEAT-1", "folder_no": ""})
    with db.Session() as s:
        o = s.query(db.Order).filter_by(source_ref="SAMPLE-REPEAT-1").one()
        assert (o.brief, o.needed_by, o.designer) == ("", "", "") and not any(m.text.startswith("Brief") for m in o.messages)


def test_dashboard_box_name_goes_to_results():
    assert c.post("/open", data={"ref": "sudeva"}, follow_redirects=False).headers["location"] == "/new?q=sudeva"
    assert c.post("/open", data={"ref": "Q-2026-10-444"}, follow_redirects=False).headers["location"] == "/orders/Q-2026-10-444"
    h = c.get("/").text
    assert "data-qsearch" in h and "/static/qsearch.js" in h
    assert c.get("/static/qsearch.js").status_code == 200


class DownOdoo:
    configured = True
    stage_note = ""

    def search_orders(self, q, limit=12):
        raise OdooError("Could not reach Odoo: timed out")

    def open_quotations(self, limit=60):
        return []


def test_odoo_not_reachable(monkeypatch):
    monkeypatch.setattr(np, "ODOO", DownOdoo())
    d = c.get("/new/search?q=sudeva").json()
    assert d["results"] == [] and "could not be reached" in d["note"]
    d = c.get("/new/search?q=achiever").json()                                             # projects on the desk still found
    assert d["results"] and d["results"][0]["on_desk"] and d["note"]
    h = c.get("/new?q=sudeva")
    assert h.status_code == 200 and "could not be reached" in h.text

    class Broken:
        def get_order(self, ref):
            raise OdooError("Could not reach Odoo")
    monkeypatch.setattr(np, "SOURCE", Broken())
    h = c.get("/new/pick?ref=Q-2026-11-900")
    assert h.status_code == 200 and "could not be reached" in h.text and "Selected" not in h.text


def test_hundred_lines_counted_not_listed(monkeypatch):
    lines, no = [], 0
    for i in range(100):
        if i < 82:
            name, kind, why = "Workstation %d (1200 x 600)" % i, "", ""
        elif i < 96:
            name, kind, why = "Chair %d" % i, "", ""
        elif i < 98:
            name, kind, why = "Installation charges", "service", ""
        else:
            name, kind, why = "Delivery and transportation", "", ""
        why = classify(name, kind)
        if not why:
            no += 1
        lines.append(SourceLine(no=0 if why else no, name=name, description="Floor %d" % (i % 3) if i % 2 else "", qty=1, no_drawing=why))

    class Big:
        def get_order(self, ref):
            return SourceOrder(source="odoo", ref=ref, client_name="Sudeva Textiles", salesperson="Anchal", lines=lines)
    monkeypatch.setattr(np, "SOURCE", Big())
    cn = np.line_counts("Q-2026-11-901")
    assert (len(cn["drawn"]), len(cn["bought"]), len(cn["aside"])) == (82, 14, 4)
    assert sum(n for _, n in cn["areas"]) == 82 and cn["areas"][-1][0] == "No area"
    h = c.get("/new/pick?ref=Q-2026-11-901").text
    assert "<b>82</b> we draw" in h and "<b>14</b> bought in" in h and "<b>4</b> set aside" in h and "By area:" in h and "Floor 1 " in h
    assert "Sudeva Textiles" in h and "The full list is in the Products tab" in h


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
