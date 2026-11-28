# Encore Drawing Desk — instructions for Claude Code

Claude Code reads this file at the start of every session.

## What this app is
The Encore Prime Furniture drawing desk (live on Railway, to be served at design.encoreprimefurniture.com). Staff turn Odoo quotations into client drawings and production drawings; clients approve or ask for changes in a client portal.

- Owner and admin: Kushal (kushal@encoreprimefurniture.com). Secondary admin: Sushil Singhal.
- Drawing engines: master copy in `kushal881/encore-engines` (tool/, templates/, library/, ENCORE_DRAWING_RULES.txt, ENCORE_PRODUCT_KNOWLEDGE.txt). The desk fetches approved engines **read-only, by version**, from `ENGINES_REPO` (default `kushal881/encore-approved`). The desk never stores or edits engine code.
- Stack: Python, FastAPI + Jinja templates (`desk/`), SQLAlchemy (Postgres on Railway via `DATABASE_URL`), Google sign-in (`desk/auth.py`), Odoo connector (`desk/sources/odoo.py`). Run: `uvicorn desk.app:app` (see Procfile). Tests: `pytest`.
- Full UI design, screens, storage plan and every decision: `docs/ui/UI_BRIEF.md` and `docs/ui/screens/`. Read the brief before any UI work.

## Hard rules
1. **Engines are locked.** Never change a file in `encore-engines` for a client's specific requirement. Client-specific changes go only in that client's product `.json`. An engine change happens only when Kushal asks, is made generic (optional, off by default), passes `engine_check.py`, is approved by Kushal, and only then is committed.
2. **The AI assistant never draws or edits a PDF.** It only changes values in the product `.json`; the engine does all drawing.
3. **Zero-error rule.** A finalized component (e.g. a leg type) never changes unless someone asks. Drawings that fail clash, true-view isometric or engine checks cannot be issued.
4. **No client files or secrets in git.** Drawings, client data, `.env`, keys stay out of the repo. Files go to the private Railway Storage Bucket (see brief).
5. Wording: "Product 1, 2…", never "Line 1".
6. **No drawing** for bought-out items (Odoo route Buy, not Manufacture), services, and installation / transportation / delivery / freight lines. The desk sets these aside before the engine (`desk/nodrawing.py`); never send them to the engine.

## How to work
- Start every new piece of work in **plan mode**: say what exists, what changes, what is new. Wait for Kushal's OK before writing code.
- **One screen or one feature per branch and pull request.** Never push straight to `main`.
- Every PR must include tests for its logic and must pass them (see Testing).
- After a PR is opened, tell Kushal what to click on the Railway staging/PR environment to check it.
- If a screen needs something the engine doesn't provide, stop and list it for Kushal. Don't work around it.
- Keep answers short and plain. Kushal is not a developer: explain what to check, not how the code works.

## Before every PR (Kushal, 28-Nov-2026)
- Check these cases and add a test for each: **old data from earlier versions** (tables, previews and drawings made by an earlier desk or engine version), **a drawing with an AI change still waiting** (Accept / Undo not pressed), **an approved or locked drawing**, **an empty or missing value**.
- Every PR description ends with a 5-line "click this, expect that" list for staging.

## Issue queue (Kushal, 28-Nov-2026)
When Kushal says "work the issue queue": read the open GitHub issues in this repo, oldest first. Fix each one on its own branch and PR (engine work: as `engine-…` branches through encore-approved). Then comment on the issue with the PR or proposal number and the staging check list. **Never close an issue yourself**; Kushal or the reviewing chat closes it after testing.

## Known state (27-Nov-2026)
- Railway (Pro plan, project `worthy-creation`, service `web`) deploys `main` automatically. A 5 GB volume is mounted at `/data`; `FILES_DIR=/data/files`, so generated files survive redeploys. Postgres and the web volume have daily, weekly and monthly backups. Moving files to the private Storage Bucket (see brief) is still planned, for the portal and signed links.
- **Run tests with `python tests/run_all.py`**, never a plain `pytest` over the whole folder: each test file sets up its own temporary database and must run in its own process. The drawing tests need `ENGINES_DIR` pointing at a copy of the approved engines.
- GitHub Actions (`.github/workflows/tests.yml`) runs all tests on every pull request against the newest approved engine version from `kushal881/encore-approved` (needs the `ENGINES_READ_TOKEN` repository secret).
- `encore-engines` already has `engine-check.yml`, `tool/regress.py` and `tool/regress_fingerprints.json`: use these as the golden-drawing check.
- Railway project `fantastic-renewal` is the separate Field Sales App. Don't touch it while working on the desk.

## Testing
1. **Unit tests** for rules: stage moves, quotation change matrix, revision raising (qty-only change after issue raises a revision), roles and permissions, portal access list, approval records.
2. **Golden drawings:** use the engines' `tool/regress.py` with `regress_fingerprints.json`. Any change must regenerate the reference drawings identically unless the change is meant to alter them.
3. **Tests run automatically** on every PR (GitHub Actions).
4. **Staging:** a separate Railway environment with its own database and test bucket, connected to test quotations in Odoo (marked TEST). Nothing reaches the live desk until Kushal approves after staging.
