"""The Guide in the desk (issue #42): the Staff Guide for everyone, the Directors Guide for the administrator and the
secondary administrator. Shown from docs/guides/staff.md and docs/guides/directors.md; Kushal changes the text by changing
those files in a pull request. A small "?" beside a page's title opens the matching section."""
import os, re
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from . import db, auth
from .app import T, User, ctx

router = APIRouter()
DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "guides")
GUIDES = {"staff": ("Staff Guide", "staff.md"), "directors": ("Directors Guide", "directors.md")}

# page address -> (guide, section anchor, what the help is about). Section anchors are s<N> from "## N. Title".
HELP = [
    (r"^/new", "staff", "s2", "Start a project"),
    (r"^/orders/[^/]+/specify", "staff", "s3", "Answer questions and specify products"),
    (r"^/orders/[^/]+/view/", "staff", "s4", "Review a drawing and make changes"),
    (r"^/orders/[^/]+/issue", "staff", "s5", "Issue drawings to the client"),
    (r"^/orders/[^/]+/client/", "staff", "s6", "When the client approves or asks for changes"),
    (r"^/orders/[^/]+/changes", "staff", "s7", "When the quotation changes in Odoo"),
    (r"^/orders/[^/]+/production", "staff", "s8", "Production drawings, bill of material and release"),
    (r"^/my-requests", "staff", "s9", "Requests to admins, and My requests"),
    (r"^/approvals", "directors", "s3", "Approvals"),
]


def can_read(user, guide):
    return guide == "staff" or auth.level(user) in ("admin", "admin2")


def help_for(path, approver=False):
    """The guide section for a page: dict(url, label), or None. Directors sections only for administrators."""
    for pat, g, sec, label in HELP:
        if re.search(pat, path or ""):
            if g == "directors" and not approver:
                return None
            return dict(url="/guide%s#%s" % ("/directors" if g == "directors" else "", sec), label=label)
    return None


def _slug(value, sep):
    m = re.match(r"\s*(\d+)\.", value)
    if m:
        return "s" + m.group(1)
    return re.sub(r"[^a-z0-9]+", sep, value.lower()).strip(sep) or "section"


def render(guide):
    """-> (html, [(anchor, section title)]) of one guide, or None when its file is missing or cannot be read."""
    import markdown
    try:
        text = open(os.path.join(DOCS, GUIDES[guide][1]), encoding="utf-8").read()
    except (OSError, KeyError):
        return None
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):                      # the page shows its own title
        lines = lines[1:]
    body = "\n".join(lines).lstrip()
    body = re.sub(r"\A(Source of truth[^\n]*\n)", "", body)       # a note for whoever edits the file, not for readers
    md = markdown.Markdown(extensions=["tables", "sane_lists", "toc"], extension_configs={"toc": {"slugify": _slug, "toc_depth": "2-2"}})
    html = md.convert(body)
    sections = [(t["id"], t["name"]) for t in md.toc_tokens if t.get("level") == 2]
    return html, sections


T.env.globals["help_for"] = help_for


def _page(request, user, guide):
    if not can_read(user, guide):
        raise HTTPException(403, "The Directors Guide is for the administrators only.")
    got = render(guide)
    return T.TemplateResponse(request, "guide.html", ctx(user, nav="guide", guide=guide, title=GUIDES[guide][0], html=got[0] if got else "",
                                                       sections=got[1] if got else [], missing=got is None,
                                                       directors=can_read(user, "directors")))


@router.get("/guide", response_class=HTMLResponse)
def staff_guide(request: Request, user=User):
    return _page(request, user, "staff")


@router.get("/guide/directors", response_class=HTMLResponse)
def directors_guide(request: Request, user=User):
    return _page(request, user, "directors")


@router.post("/guide/dismiss")
def dismiss(user=User):
    """Hides the dashboard's 'New here? Read the guide' card for this person."""
    with db.Session() as s:
        s.get(db.User, user.id).guide_dismissed = True
        s.commit()
    return RedirectResponse("/", status_code=303)
