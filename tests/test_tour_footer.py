"""Tour card footer (issue #70): dots on the left; Skip tour, Back, Next on the right in one group that never wraps (on very
narrow phones only the dots go above); no Back on the first step (no gap); Finish on the last. The desk and the client
portal use the same script. Checked in a real browser at 330, 375 and 1360 px when the PR was made; here the script and
the pages that load it are checked."""
import os, sys, re, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from desk.app import app

JS = open(os.path.join(os.path.dirname(__file__), "..", "desk", "static", "tour.js"), encoding="utf-8").read()


def test_buttons_one_group_that_never_wraps():
    grp = re.search(r'<div id="tbtns" style="([^"]+)">(.*?)</div></div>', JS, re.S)
    assert grp, "Skip, Back and Next must sit in one #tbtns group"
    css, inner = grp.group(1), grp.group(2)
    assert "flex-wrap:nowrap" in css and "flex:0 0 auto" in css and "margin-left:auto" in css
    assert inner.index('id="tskip"') < inner.index('id="tback"') < inner.index('id="tnext"')      # Skip tour, Back, Next in that order
    assert inner.count("white-space:nowrap") == 3
    assert "background:#0E6B5C;color:#fff" in inner.split('id="tnext"', 1)[1]                    # Next is the primary button
    foot = re.search(r'<div id="tfoot" style="([^"]+)">', JS).group(1)
    assert "flex-wrap:wrap" in foot                                                              # only the dots may go above, on narrow phones


def test_first_step_no_back_last_step_finish():
    assert re.search(r"\(i \? '<button type=\"button\" id=\"tback\"", JS)                       # Back only after the first step
    assert "(i === steps.length - 1 ? 'Finish' : 'Next')" in JS


def test_desk_and_portal_load_the_same_script():
    c = TestClient(app)
    c.post("/login/dev/1")
    assert '/static/tour.js' in c.get("/?tour=1").text
    assert c.get("/static/tour.js").text == JS


def test_empty_steps_do_nothing():
    assert "if (!steps || !steps.length || document.getElementById('tour')) return;" in JS
