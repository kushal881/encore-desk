# Encore Drawing Desk — UI rework brief

Approved design: https://claude.ai/artifact/T31tHHmvQ3XC1KP48Nfk6Z
The `screens/` folder holds the markup of each screen (`*.dc.html`). They are **design references**: copy the layout, wording, colours and flow into the existing app. They do not run on their own.

## Hard rules (do not break)
1. **Engines are locked.** Never change any file from `kushal881/encore-engines` for this work. If a screen needs something the engine doesn't give, stop and list it for Kushal.
2. **The AI never draws or edits a PDF.** It only changes values in the product's `.json`. The engine does all drawing.
3. Wording: "Product 1, 2…", never "Line 1".
4. Every page has a **Back** button and the same left menu.
5. Every page shows **one clear next step**.
6. Build **one screen at a time**. Test it on Railway, then commit, before starting the next.

## Look
- Font Inter. Background #F4F3EF, cards white with #E3E1DA border, 12 px radius.
- Ink #17191C, muted #5B5F66. Primary teal #0E6B5C (light #E3F1EE).
- Amber = needs action (#9A4508 on #FDF1E2). Red = locked/blocked (#B91C1C on #FDE8E8). Blue = review/AI (#1D4ED8 on #E6EDFB).
- Buttons and inputs at least 44 px tall.

## Stages (used everywhere)
1 Order → 2 Specify → 3 Generate → 4 Review → 5 Issue → 6 Client approval → 7 Production → 8 Done

## Screens
### Desk (staff and admin)
| # | File | What it does |
|---|---|---|
| 1 | Main | Dashboard. Clickable counts. "Your next steps" list. Active projects table. **Admin only:** Engine requests tile, Approvals menu, approval steps. **Staff:** "My requests" menu + approved/rejected task items. |
| 2 | NewProject | Start from an Odoo quotation (only open opportunities, not won/lost) + server folder no. Or **remake from a past order**: pick approved drawings, copy their spec into a new project. |
| 3a | Projects | All-projects dashboard: 8 stage tiles (click to filter), Active / Completed / All tabs, salesperson filter, search, table with mini stage bar, drawings done/total, needs-attention flag, last activity. Open → Project. |
| 3 | Project | Single project. Stage bar + one big Next button. Products with Odoo photos. Project files in 3 groups: client drawings (PDF, DXF, combined set), production (production PDF incl. pin-up screen detail, production DXF, BOM Excel, cut list), other (Other, Old Revisions). Red bar when quotation changes are waiting. Back → Projects. |
| 4 | Specify | Draft built from Odoo with standard rules first; AI asks **only what is missing**. **Area per product** (optional, several areas each with a qty; must add up to product qty). "Engine can't build" → Request engine change. |
| 5 | Viewer | Client drawing in the app. Zoom, client/production switch, numbered markers, automatic checks. **AI change panel** (marker-linked → proposed value change → engine redraws → Accept / Undo / Compare before–after). Stage note. |
| 6 | Issue | Step 5: pick drawings; **email to client** with template picker (first issue / revised / additional / cancelled), To, CC, subject, message with **portal link**, PDF attach optional. **Portal access list**: only listed emails can sign in; "Give access to another email" (view & comment / approve) + invite. Step 6: approvals arrive from the portal per drawing; manual record for email/phone replies; approval locks client drawings. Step 7: job order, deadline, production drawings, BOM, cut list → opens Production. |
| 11 | Production | Production sheet (no client details, job order + deadline). Tabs: production drawing (incl. pin-up screen detail), BOM, cut list. Copy-for department picker. **AI change panel limited to construction** (frame, supports, joinery, hardware placement, edge band, cut sizes); shows BOM effect; P1 → P2 needs admin approval. Design changes (size, finish, qty) are refused → RevB route. Release to production. |
| 7 | Approvals | Admin only. Engine changes / changes after client approval / revision raises. Option A (client only → product `.json`) or B (generic engine option, off by default). Decision **creates a task** for the requester. |
| 7a | MyRequests | Staff. Summary tiles Pending / Approved / Rejected / Done. Each request shows admin decision + the task on that drawing. Approved → "Apply & regenerate" → drawing to Review. Rejected → reason + "Mark task done". |
| 8 | Changes | Quotation changes inbox (Added / Dropped / Qty / Spec / Price-only), lock banner, "Quotation needs update" to sales, cancelled stamp, history. |

### Client portal (client-facing, Encore-branded, no desk menu)
| # | File | What it does |
|---|---|---|
| 8a | PortalLogin | Email + one-time 6-digit code (no password), keep signed in 30 days. **Only emails on the order's access list can sign in.** Opening the email link signs in directly. |
| 8b | PortalHome | Client's orders: summary tiles, active quotations/orders with drawing summary bar (approved / changes asked / waiting), past orders table. |
| 8c | PortalPast | Past orders: list + detail (products, finish, qty, drawing sheets), final set download, tick products → "Ask to reorder" (goes to sales person). |
| 9 | PortalSet | One order's drawing set: cards with status, approve remaining (name, designation, one-time code, tick). |
| 10 | PortalDrawing | One drawing: zoom, approve (name + one-time code) or ask for change with numbered marks, notes and attachment. |

Logo: `encore-logo.png` (in this folder) on the desk menu and the portal header.

## Quotation change rules
- Products are tied to the **Odoo quotation line ID**. Product numbers and drawing serials never shift and are never reused.
- Check Odoo when a project opens and once a day.

| Drawing stage | Added | Dropped | Qty / spec changed |
|---|---|---|---|
| Not drawn | Specify | Remove | Use new values |
| Drawn, not issued | Specify | Archive | Regenerate, same rev |
| Issued to client | Specify, issue as addition | Mark Cancelled, reissue set | New revision (ask once) |
| Client approved | Specify, needs client approval | Cancel, admin permission | Locked: admin permission, new rev, re-approval |
| In production | Separate production set | Stop alert to production + admin | Stop alert, redo production drawings + BOM |

Price-only changes are ignored. Every change redoes the combined PDF, the sheet numbers (N / total) and the BOM, and is logged (who, when, what, which revision).

## Storage (decided 27-Nov-2026)
- **Files:** one **private Railway Storage Bucket** (S3-compatible), same project as the desk. Never a Railway volume, never GitHub, never the app's own disk.
- **Layout in the bucket:** one folder per client `Encore-<Mon>-<YEAR>-<server folder no>-<Company>/` with `PDF/`, `DXF/`, `Other/`, `Old Revisions/`, `Bill of Material/` (when made), plus a hidden `_previews/` folder the desk uses. Same file names and revision rules as now (`…-Rev<X>`).
- **Security:** bucket is private. The desk/portal checks the user (staff role, or client email on that order's access list) and then hands out a **short-lived signed link** (a few minutes). Log every view, download and approval.
- **Speed:** when the engine makes a drawing, also save a light **preview image per sheet** in `_previews/`. Viewer shows the preview instantly; full PDF loads only on deep zoom or download.
- **Database** (projects, revisions, approvals, tasks, access lists, history): Railway database with Railway's built-in backups on.
- **Office copy:** a small sync script on an office PC/server **pulls** new/changed files from the bucket into the office share (Design / Production folders) every night and on each issue. Pull only — no ports opened on the office network.

## Suggested build order
1. Shared layout: left menu, back button, colours.
2. Dashboard (1) and Project page (3) with the stage bar.
3. Viewer (5): zoom, markers, checks. Add the AI panel after that.
4. Specify (4), Issue (6), Approvals (7).
5. New project / Remake (2).
6. Changes inbox (8), which needs saved quotation versions.
7. Storage bucket + previews + signed links (do this before the portal).
8. Client portal (8a → 8b → 8c → 9 → 10), Projects (3a), MyRequests (7a), Production (11).

## Decisions log
| # | Topic | Decision |
|---|---|---|
| 1 | Reading products from Odoo | Read the **quotation line description** for now (not Odoo attributes directly). Many descriptions are auto-built from Odoo attributes, so they follow a fixed pattern: parse them with fixed rules (no AI). If a line doesn't match the pattern, flag it for the user to fill in on Specify. Switching to reading attributes directly can come later. (27-Nov-2026) |
| 2 | Change detection | Odoo **automation rule** on the Sales Order (on save, when order lines, quantities or state change) sends a webhook to the desk → instant. Safety net: desk also re-checks a quotation when its project is opened, and runs a full nightly check. |
| 3 | Job order & deadline | Read from the Odoo sales order: **Job Order No** (auto-filled when the quotation is confirmed, e.g. JO 26-27-11-0119) and **Delivery Date** (entered manually by sales). If Delivery Date is empty, the Production step is blocked with "Ask sales to enter the delivery date in Odoo". |
| 4 | Qty-only change after issue | **Raises a revision** (quantity is in the title block). |
| 5 | Backup approver | **Sushil Singhal** (secondary admin). |
| 6 | Retention & backups | Keep everything **forever** for now. Backup policy to be decided later, once live. |
| 7 | Staff sign-in | **Google Workspace sign-in**, restricted to @encoreprimefurniture.com accounts; 2-step verification enforced in Workspace admin. No desk passwords. Clients: email one-time code (portal). |
| 8 | Roles | **Admin:** Kushal (everything). **Secondary admin:** Sushil Singhal — approves requests and day-to-day changes, but **no major-change rights** (no engine changes, no role changes, no unlocking client-approved drawings). **Sales:** projects, quotation changes, client emails, **can give clients portal access**. **Designers:** specify, generate, review, change requests. **Production:** view production sets, BOM, cut lists; receive stop alerts. |
| 9 | Sending address | One shared address: **info@encoreprimefurniture.com** (Google Workspace). |
| 10 | Web address | **design.encoreprimefurniture.com**, part of the desk app (desk + client portal on the same app). |
| 11 | Client approval proof | Portal approval = click-wrap e-contract: client named on the access list, one-time code to that email, tick "I approve this drawing (RevX) for manufacturing". Store an **approval record**: drawing no. + revision + file fingerprint (hash), name, designation, email, date/time, IP, code-verified flag. Email the client a **confirmation with the approved PDF** attached. Quotation T&C to state that drawings approved on the portal are binding (lawyer to confirm wording). Optional later for high-value orders: Aadhaar eSign. |
| 12 | Stop alerts | Sent when a quotation changes **after production has started**. By **email** now, plus an **in-app notification** to all related users: admins (Kushal, Sushil), the project's salesperson and production users. |
| 13 | Office copy | **No sync to the office server for now** (it is ~90% full). The desk is where everyone finds drawings (Project files panel + "Download folder as zip"). Revisit after the vendor confirms the server type. |
| 14 | Running cost | Railway usage limits: custom email alert + hard limit on the Workspace Usage page. Separate monthly spend limit for the AI assistant's API key. |
| 15 | Lines with no drawing | **Chairs and other bought-out items** (Odoo product route = Buy, not Manufacture), **services** (Odoo product type = service) and **installation, transportation, delivery, freight** and similar words in the product name get **no drawing**. They are not added as products to draw, are not numbered "Product N", never get questions or engine requests, and are listed on the project page under "No drawing needed". Handled in the desk (`desk/nodrawing.py`), not in the engines. (27-Nov-2026) |
| 16 | Add / remove product lines | On the project page any signed-in user can **remove** a product from drawings (with a reason: bought out after all, not needed by client, duplicate, other). It moves to "No drawing needed" with their name; nothing is deleted. A product with an issued drawing cannot be removed this way (that is a quotation change). Lines in "No drawing needed" (except installation/delivery and services) can be **added back**; a removed product gets its old number back, others get the next number. A product **not in the quotation** can be added (name, qty, details): next number, and "Quotation needs update" is shown for the salesperson. Product numbers are never reused. (27-Nov-2026) |
