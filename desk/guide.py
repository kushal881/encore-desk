"""The Guide in the desk (issue #42): the Staff Guide for every signed-in person, the Directors Guide for the
administrators (Kushal and the secondary administrator) only. Rendered from docs/guides/staff.md and directors.md, which
Kushal edits through a pull request; no editor in the app. A contents list links to each section; every page's "?"
opens the matching section. A small renderer for what the guides use (headings, numbered and bulleted lists, tables,
bold, code); everything is escaped first, so the text can never break the page."""
import html, os, re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup
from . import db, auth
from .app import T, User, ctx

router = APIRouter()
DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "guides")
GUIDES = {"staff": ("staff.md", "Staff Guide"), "directors": ("directors.md", "Directors Guide")}
# the "?" next to page titles -> (guide, section anchor)
HELP = {"new": ("staff", "start-a-project"), "specify": ("staff", "answer-questions-and-specify-products"),
        "viewer": ("staff", "review-a-drawing-and-make-changes"), "issue": ("staff", "issue-drawings-to-the-client"),
        "production": ("staff", "production-drawings-bill-of-material-and-release"), "changes": ("staff", "when-the-quotation-changes-in-odoo"),
        "approvals": ("directors", "approvals"), "mine": ("staff", "requests-to-admins-and-my-requests")}


def slug(text):
    """'## 2. Start a project' -> 'start-a-project' (the number is left out, so renumbering keeps links working)."""
    t = re.sub(r"^\d+[.)]\s*", "", text.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<i>\1</i>", t)
    return t


def render(md):
    """-> (html, [(anchor, title)] of the ## sections)."""
    out, toc, lst, table, para = [], [], None, [], []

    def flush():
        nonlocal lst, table, para
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para))); para = []
        if lst:
            out.append("</%s>" % lst); lst = None
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?\s*:?-{2,}.*", r.replace(" ", ""))]
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            h = "<div class='scroll'><table><tr>%s</tr>" % "".join("<th>%s</th>" % _inline(c) for c in cells(rows[0]))
            h += "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in cells(r)) for r in rows[1:]) + "</table></div>"
            out.append(h); table = []
    for line in md.splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2).strip()
            a = slug(text)
            if level == 2:
                toc.append((a, text))
            out.append("<h%d id='%s'>%s</h%d>" % (min(level + 0, 4), a, _inline(text), min(level, 4)))
            continue
        if s.startswith("|"):
            if not table and (para or lst):
                flush()                                                  # a table starts: close the paragraph or list before it
            table.append(s); continue
        elif table:
            flush()
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", s) or re.match(r"^\s*([-*])\s+(.*)$", s)
        if m:
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            if para:
                out.append("<p>%s</p>" % _inline(" ".join(para))); para = []
            if lst != kind:
                if lst:
                    out.append("</%s>" % lst)
                out.append("<%s>" % kind); lst = kind
            out.append("<li>%s</li>" % _inline(m.group(2))); continue
        if not s.strip():
            flush(); continue
        if lst and line.startswith(("   ", "\t")):                       # a continued list item
            out[-1] = out[-1][:-5] + " " + _inline(s.strip()) + "</li>"; continue
        if lst:
            out.append("</%s>" % lst); lst = None
        para.append(s.strip())
    flush()
    return "\n".join(out), toc


def can_read(user, which):
    return which == "staff" or auth.level(user) in ("admin", "admin2")


def load(which):
    """-> (html, toc) of one guide, or (None, []) when its file is missing."""
    path = os.path.join(DOCS, GUIDES[which][0])
    if not os.path.exists(path):
        return None, []
    return render(open(path, encoding="utf-8").read())


@router.get("/guide", response_class=HTMLResponse)
@router.get("/guide/{which}", response_class=HTMLResponse)
def guide(request: Request, which: str = "staff", user=User):
    if which not in GUIDES:
        raise HTTPException(404)
    if not can_read(user, which):
        raise HTTPException(403, "The Directors Guide is for the administrators")
    body, toc = load(which)
    return T.TemplateResponse(request, "guide.html", ctx(user, which=which, title=GUIDES[which][1], body=Markup(body) if body else None, toc=toc,
                                                          both=can_read(user, "directors"), nav="guide"))


@router.post("/guide/dismiss")
def dismiss(user=User):
    """Hides the dashboard's "New here? Read the guide" card for this person."""
    with db.Session() as s:
        u = s.get(db.User, user.id)
        u.guide_dismissed = True; s.commit()
    return RedirectResponse("/", status_code=303)
