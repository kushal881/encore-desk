# Encore Drawing Desk — Directors Guide

Source of truth for the in-app Directors Guide (28-Nov-2026). Shown to admins only (Kushal, Sushil).

For Kushal and Sushil. Staff work is in the Staff Guide; this covers decisions, approvals, engines and keeping the desk running.

## 1. Who can do what

Kushal decides everything; Sushil approves day-to-day work but cannot make major changes.

| Role | Who | Can | Cannot |
| --- | --- | --- | --- |
| Admin | Kushal | Everything: approvals, engine changes, unlocking client-approved drawings, roles, settings | — |
| Secondary admin | Sushil Singhal | Approve requests and day-to-day changes, backup approver when Kushal is away | Engine changes, role changes, unlocking client-approved drawings |
| Sales | e.g. Anchal, Twinkle | Projects, quotation changes, client emails, give clients portal access | Approvals |
| Designer | drawing staff | Specify, generate, review, change requests | Approvals, unlocking |
| Production | factory | View production sets, BOM, cut lists; receive stop alerts | Change client drawings |

**Adding a new employee:** use the Users screen (admin menu) once it is built (issue #40); until then ask Claude to add them. Every sign-in uses the employee's @encoreprimefurniture.com Google account; keep 2-step verification on in Google Workspace admin. Someone who leaves: turn off their Google account, and they can no longer sign in.

## 2. Daily 5-minute check

Open **portal.encoreprimefurniture.com** each morning and read the dashboard top to bottom.

1. **Counts at the top** (all clickable): Active projects, Drawings pending, Waiting for client approval, Approved not yet released, In production, Engine requests waiting. Anything stuck for more than 2 days → ask the person named on the project.
2. **Client asked for changes (N):** clients waiting on us. Make sure each is applied or answered the same day.
3. **Approvals** (menu, shows a count): decide everything waiting (section 3).
4. **Engine requests** (menu, Kushal): new requests from staff.
5. **Engines box:** the version in use. When a newer approved version exists, update it (section 4).
6. **Red bars on projects:** a quotation changed in Odoo. Production after a change gets a stop alert by email; make sure someone acts on it.

Once a week: **Projects** → filter by salesperson to see who has stuck work, and **Completed** to check finished projects were closed with **Mark project completed**.

## 3. Approvals

Menu → **Approvals** lists everything staff can't do alone. Click **See the change** before deciding. Every decision becomes a task for the person who asked, in their **My requests**.

| Request | What it means | Buttons |
| --- | --- | --- |
| Change after client approval | The client wants a change to a drawing they already approved | **Allow** (unlocks; issuing makes a new revision the client approves again) or **Reject** with a reason |
| New production revision (P1 → P2) | Construction change to a set already released to the factory | **Allow** or **Reject**; production gets a stop alert and new sheets if allowed |
| Engine request | The engine can't build something a client needs | **Approve option A** = this client's product only, no engine work. **Approve option B & send to engine work** = a general option for all future drawings, off by default. Or **Reject** |

**Rule of thumb for engine requests:** choose A when it's a one-off for this client. Choose B only when the same need will come again (for example 65 mm grommets on every table). B becomes an engine issue that is built, checked and approved before anyone can use it.

**Clients and production never wait for engine fixes.** Approve A so the drawing can go out today, and choose B as well if the engine should learn it later.

Sushil can approve change requests and P2 when Kushal is away. Only Kushal can approve engine changes or unlock client-approved drawings.

## 4. Engines: approving and updating a version

The engines do all the drawing. They are locked: nobody changes them for one client, and the desk only ever uses an **approved** version (v16 on 28-Nov-2026).

**How a new engine version reaches the desk:**

1. A need is written as a GitHub issue in **kushal881/encore-engines** (from an Option B approval, a client chat, or you).
2. Claude Code builds it on its own `engine-…` branch, generic and off by default, and it must pass the engine checks and golden-drawing test.
3. In **kushal881/encore-approved** → Actions → **approve** → **Run workflow** (branch main, leave "FIRST RUN ONLY" unticked). This turns each ready branch into a proposal pull request.
4. Open the proposal, read its check list, and click **Merge**. It is tagged as the next version (v17…).
5. On the desk, the Engines notice offers the new version; click **Update now**.
6. On the office laptop, run **Update engines.bat** so client-chat drawings use the same version.

**Proposals show a conflict?** That is normal after another one is merged. Run step 3 again to refresh them, then merge.

**Already issued drawings stay on the engine they were made with.** Staff can choose **Redraw on vN** for a drawing that isn't approved yet.

## 5. Building the desk further

New features and fixes go through GitHub issues, so nothing reaches the live desk untested.

1. **Ask for it:** write what you want in plain words in a GitHub issue in **kushal881/encore-desk** (or ask the reviewing Claude chat to write it).
2. **Build:** tell the Claude Code session **"work the issue queue"**. It fixes each issue on its own branch and opens a pull request with tests and a 5-line "click this, expect that" check list.
3. **Test:** GitHub runs all tests automatically (green tick). The reviewing chat points **staging** (its own database and test data) at the change and clicks through the check list.
4. **Merge:** when staging passes, you click **Merge pull request**. The live desk redeploys by itself in about 5 minutes, only after the tests pass.
5. **Close the issue** after you have seen it working on live. Claude never closes issues itself.

**Several pull requests touching the same menu** can clash. Ask the reviewing chat to combine them into one tested pull request, then merge that one.

**Hard rules that never change:** engines are locked; the AI never draws or edits a PDF; client files and passwords never go into GitHub; one feature per pull request; never push straight to main.

## 6. Where everything lives

The desk runs on Railway project **worthy-creation**; everything below is already set up and runs by itself.

| What | Where | Runs by itself? |
| --- | --- | --- |
| The desk + client portal | https://portal.encoreprimefurniture.com (Railway service **web**, production) | Redeploys when a pull request is merged |
| Staging (test copy) | Railway environment **staging**, own database | Follows whichever branch is being tested |
| Database (projects, approvals, history) | Railway Postgres | Daily, weekly, monthly backups |
| Drawing files | Private storage bucket (+ working copy on the web volume) | Opened through 5-minute signed links; every view logged at /storage |
| Code | GitHub kushal881/encore-desk | Tests run on every pull request |
| Engines | kushal881/encore-engines (workshop) → kushal881/encore-approved (approved versions) | No; you approve each version |
| Domain | BigRock: CNAME **portal** → Railway | — |
| Staff sign-in | Google Cloud project Encore Desk (OAuth client) | — |
| Email | info@encoreprimefurniture.com through Google Workspace (app password in Railway) | — |
| Odoo → desk | Odoo automation rule **Drawing desk: quotation changed** (webhook) | Every save of a quotation |
| Nightly Odoo check | Railway service **nightly-odoo-check** | Every day 05:30 India time |
| AI assistant | Anthropic API key in Railway, monthly spend limit | — |

**Secrets** (passwords, keys, the webhook token) live only in Railway variables. Never paste them in chat, email or GitHub. If one leaks, make a new one and replace it in Railway (and in Odoo for the webhook token).

## 7. If something breaks

| What you see | Likely cause | What to do |
| --- | --- | --- |
| Desk shows "Internal Server Error" after a merge | The new version has a fault | Railway → web → Deployments → on the previous good one, ⋮ → **Redeploy**. Then open an issue. |
| Railway email "Deploy Crashed" | A service failed to start or a nightly run failed | Open the service's logs in Railway, send a screenshot to the reviewing chat |
| Staff can't sign in | Not on the user list, or wrong Google account | Add them (section 1); check they use @encoreprimefurniture.com |
| Emails show "not sent" | info@ app password changed or 2-step turned off | Make a new app password for info@ and replace SMTP_PASSWORD in Railway (production and staging) |
| Quotation changes stop appearing | Odoo rule turned off, or webhook token changed | Odoo → Settings → Technical → Automation Rules: check the rule is active; the nightly check still catches changes overnight |
| Client gets no portal code | Email not on the order's access list, or in spam | Check the access list spelling; ask them to check spam |
| Railway bill rising | Usage | Railway → Workspace → Usage: set an email alert and a hard limit there |

**Golden rule:** never fix live by hand in a hurry. Roll back to the last good deployment, then fix it properly through an issue and staging.
