# Encore Drawing Desk — Staff Guide

Source of truth for the in-app Staff Guide (28-Nov-2026). Kushal edits the shared doc; copy changes here.

## 1. Getting started

The desk is where every Encore drawing is made, sent to the client, approved and released to production. You never draw by hand and never edit a PDF: you answer questions, the engine draws.

1. Open **https://portal.encoreprimefurniture.com** on a laptop or phone.
2. Click **Sign in with Google** and pick your **@encoreprimefurniture.com** account. No password for the desk itself.
3. If it says you are not on the list, ask Kushal to add you. Only listed staff can sign in.

**The left menu** is the same on every page: Dashboard, New project, Past orders / Remake, Projects, My requests (staff), Approvals and Engine requests (admins only). Every page has a **Back** button at the top.

**Every project moves through 8 stages**, shown as a bar at the top of the project:

1. Order — the Odoo quotation is on the desk
2. Specify — questions answered, sizes and finishes set
3. Generate — the engine draws the preview
4. Review — you check the drawing
5. Issue — sent to the client
6. Client approval — the client approves or asks for changes
7. Production — production drawings, bill of material, cut list
8. Done

**Colours mean the same everywhere:** amber = you need to act, red = locked or blocked, blue = AI change or review, teal = the next step button.

**Words we use:** products are always "Product 1, 2, 3…" (never "Line 1"). Product numbers and drawing numbers are never reused, even if a product is removed.

**Golden rule:** each page shows one clear next step. If you are unsure what to do, do the big teal button.

## 2. Start a project

Every project starts from an Odoo quotation, or from a past order the client wants again.

**From an Odoo quotation**

1. Menu → **New project** (or the dashboard's **New project or remake ›**).
2. On the left, **From an Odoo quotation**, search by quotation number or client. Only quotations whose opportunity is still open are listed; won/lost ones and quotations already on the desk are hidden.
3. Tick the quotation and type the **Server folder no.** (for example 05).
4. Click **Create project & read products**. The desk reads every product line from Odoo.
5. Chairs and other bought-out items, services, installation, delivery and freight lines go to **No drawing needed** automatically. They are not numbered as products.

**Remake from a past order** (client wants the same product again)

1. Menu → **Past orders / Remake**.
2. On the right, search past drawings. Only client-approved drawings are listed.
3. Tick the drawing(s), pick the new quotation under **Link to new quotation** (or type a server folder no.).
4. Click **Copy products into new project**. The approved specification is copied; the copies are drawn at once and wait for your review.
5. If the new quotation has no product with the same name, the salesperson is told to add it in Odoo.

**Find a project later:** Menu → **Projects**. Click a stage tile to filter, use Active / Completed / All, filter by salesperson, or search by client, quotation or folder. Click **Open**.

## 3. Answer questions and specify products

The desk fills in everything it can from Odoo and Encore's standard rules. It only asks you what is missing.

1. Open the project. The big button says **Answer N questions ›**.
2. Each question names the product (for example "Product 6. Which side is the shutter hinged?"). Click one of the offered options, or type in **Your answer**, then click **Answer**.
3. Not sure yet? Click **Skip for now** and come back later. The product cannot be drawn until its questions are answered.
4. **Areas (optional):** on Specify, click **+ Add area** to split a product's quantity by area (for example 30 in Open office, 26 in Training room). The areas must add up to the product quantity. Click **Save areas**.
5. When a product is ready, click **Draw the preview →**. The engine draws it (stage 3, Generate).

**If a product looks like a past one,** the project page shows **Same as a past drawing**. Use **Remake exactly** to copy that approved specification instead of answering again.

**Remove a product from drawings** (bought out after all, not needed, duplicate): on the project page remove it and give the reason. It moves to **No drawing needed** with your name. Nothing is deleted, and it can be added back. A product that has already been issued cannot be removed this way; that is a quotation change (section 7).

**Add a product that is not in the quotation:** add it on the project page with name, quantity and details. It gets the next number and the salesperson is told **Quotation needs update**.

**The engine cannot make what the client wants?** Click **Request engine change** (or **Ask for an engine addition**) and describe it. Never work around it. An admin decides (section 9).

## 4. Review a drawing and make changes

Every drawing is checked by you in the viewer before it goes to a client.

1. Open the drawing (from the project page, **Open drawing in viewer →**). Use **+ / −** and **Fit** to zoom, **‹ ›** for sheets, and **Client drawing / Production drawing** to switch views.
2. Read the automatic checks (clash, true-view isometric, engine checks). A drawing that fails a check cannot be issued.
3. **To change something:** click **+ Add numbered marker** on the spot, then type what should change in the AI change panel (for example "make the length 1500") and **Send**.
4. The AI shows what it understood (for example "Length 1200 → 1500, frame 1180 → 1480") and the engine redraws. Use **Compare** to see before and after.
5. Click **Accept** to keep it, or **Undo** to throw it away. **A drawing with a change still waiting (Accept / Undo not pressed) cannot be issued.**
6. When it is right, click **Looks right — mark ready to issue**, then **Go to issue**.

**What the AI can and cannot do:** it only changes values in the product's settings; the engine does all drawing. It never edits a PDF. A finalised part (for example a leg type) never changes unless someone asks.

**Drawing already approved by the client?** It is locked. Click **Ask for permission**; an admin decides. If allowed, the change becomes a new revision that the client must approve again.

**Old drawing on an older engine?** It stays on its engine version. **Redraw on vN** updates it only if you choose to.

## 5. Issue drawings to the client

Issuing gives the drawings a revision letter (Rev A, B…), makes one combined PDF in serial order, and emails the client from info@encoreprimefurniture.com.

**First, give the client portal access** (project → **Issue and approval** → **Portal access for this order**):

1. Under **Give access to another email**, type the client's email and name.
2. Pick **Approve** (can approve drawings) or **View & comment**. Click **Add**.
3. Only these emails can sign in to the client portal. CC addresses and forwarded mails cannot. Use **Remove** to take access away. Every change is logged.

**Then issue and send:**

1. Tick the drawings to send and click **Issue the drawings picked**. If it refuses, it tells you why (questions open, no preview, an AI change waiting, a check failed).
2. Under **Email to client**, the template is picked for you: Drawings for approval (first issue), Revised drawings, Additional products or Cancelled products. Change it if needed.
3. Fill **To** (people on the access list) and **CC**. Check the subject and message.
4. Keep **Attach the combined PDF** ticked unless the client asked for no attachment.
5. Click **Send email**. The log below shows **sent**, or **not sent** with the reason.

Each person on the access list gets their own email with a personal portal link (valid 14 days). CC gets a copy with only the plain portal address.

**Client approved by phone or email instead of the portal?** Under **Client approval**, use **Record**: pick the answer, add a note, attach the **proof** (their email or signed PDF). Approval locks the client drawing.

**Templates in one line each:** Drawings for approval = first time. Revised drawings = a new revision after changes. Additional products = new products added to an issued set. Cancelled products = products dropped from the quotation.

## 6. When the client approves or asks for changes

Clients answer on the portal: they approve with a one-time code, or mark numbered spots on the drawing and say what to change. Their answers appear in the **Client approvals** panel on the project, separate from the conversation. The dashboard shows **Client asked for changes (N)** when something is waiting.

**Client approved:** nothing to do. The drawing is locked and the client gets a confirmation. When every issued drawing is approved, the project moves to production.

**Client asked for changes:**

1. Open the project and find the card **Product N · drawing no. Rev X · Changes asked**. It lists the client's numbered notes, for example "[1] Increase size to 1050mm".
2. Click **View the marked-up drawing** to see where they marked.
3. Check the text under **Change for the change panel** (edit it if the client's words are unclear) and click **Apply change**. It goes to the AI change panel.
4. In the viewer, check the redraw and click **Accept** (or **Undo**). Until you do, the card says **Accept or Undo it, then re-issue**.
5. Click **Re-issue to client ›**. The page shows the new revision (for example Rev C), who it goes to, the subject and message.
6. Click **Issue Rev C and send**. The combined PDF is made again and each person on the access list gets their own portal link.

**Not changing the drawing?** Under **Reply to the client or decline**, type an answer (the client sees it on the portal) and click **Reply** or **Decline the change**.

**Every step is logged** on the card: who applied, re-issued, replied or declined, and when.

## 7. When the quotation changes in Odoo

When sales saves a change to a quotation in Odoo (products, quantities, status, delivery date, job order no.), the desk knows within seconds. It also checks when a project is opened, when you press **Check Odoo now**, and every night.

1. A red bar appears on the project, and **Quotation changes** shows a number. Issue and production are locked until every change is handled, so drawings and quotation always match.
2. Open **Quotation changes**. Each change says what happened (Added, Dropped, Qty changed, Spec changed) and offers the right button for the drawing's stage.
3. Price-only changes are ignored.

| The drawing is… | Product added | Product dropped | Qty or spec changed |
| --- | --- | --- | --- |
| Not drawn yet | Add & specify the new product | Removed | Use the new values |
| Drawn, not issued | Add & specify | Archived | Redrawn, same revision |
| Issued to client | Specify, issue as an addition | Mark cancelled & reissue | Redraw for a new revision |
| Client approved | Specify, needs client approval | Cancel: Ask admin permission | Locked: Ask admin permission, new revision, client approves again |
| In production | Separate production set | Stop alert to production + admin | Stop alert, production drawings + BOM redone |

**Ignore** an entry only when it really doesn't affect the drawing (for example a test change that was put back).

**The desk needs Odoo to change** (you added a product that isn't in the quotation): the **Quotation needs update** box lists it. Click **Notify salesperson**.

**Cancelled drawings** get a red CANCELLED stamp with the date and quotation version. Their number is retired and never reused; they leave the combined PDF and the sheet numbers are redone.

## 8. Production drawings, bill of material and release

Production sheets are made from each product's latest **issued** client revision. They carry the job order and deadline and **no client details**.

**Make the production set** (project page, under **Production drawings**):

1. Check **Job order number** and **Job order date**. They come from Odoo's Job Order No. once the quotation is confirmed.
2. Check **Production deadline**. It comes from Odoo's Delivery Date. If Odoo has none, ask sales to enter it in Odoo, or type it here.
3. Click **Make production drawings**. For outside work (powder coating, pin-up screens) use **Make subcontractor drawings** the same way.
4. Under **Bill of material and cut list**, tick **Panel-saw cutting** for the factory panel saw (4 mm saw cut, cut sizes, grain kept along the board, fewest boards) and click **Make the bill of material**. You get an Excel file.

**Check and release** (Production screen for each product):

1. Read the production drawing; switch tabs for drawing, bill of material and cut list. Use **Copy for** to pick the department and print or send.
2. **Construction changes only** (frame, supports, joinery, hardware placement, edge band, cut sizes): add a marker, type the change, **Send**, check **See after**, then **Accept** or **Undo**. The desk checks the client drawing is unchanged.
3. **Design changes** (size, finish, quantity) are refused here. They need a new client revision: click **Ask admin permission**.
4. Click **Release P1 to production**. Production gets the sheets.
5. A released set changed later becomes **P2**. It needs admin approval, and production gets a **stop alert** with the new sheets.

**Before the client approves:** a release can happen early only with a written reason (for example "URGENT, before client approval"). It is logged on the project, and the project shows **Client approval is still pending** until the client approves.

## 9. Requests to admins, and My requests

Some things need an admin's yes: an engine addition, a change to a client-approved drawing, or a new production revision (P2). You ask, an admin decides, and the decision comes back to you as a task.

**Ask:**

- Engine can't build it → **Request engine change** / **Ask for an engine addition** on Specify or the project page. Describe what the client needs.
- Drawing is locked (client approved) → **Ask for permission** in the viewer, or **Ask admin permission** on Quotation changes or Production.

**Follow up:** menu → **My requests**. Tiles show Pending, Approved, Rejected and Done.

- **Approved:** the task is on that drawing. Apply the change, check the redraw, then continue as normal (review, issue).
- **Rejected:** read the reason, tell the client or salesperson if needed, then click **Mark task done**.

**What an admin can choose for an engine request:** Option A = only this client's product changes (fast, no engine work). Option B = a new general engine option, off by default, built and checked before anyone can use it. While waiting, the client's drawing can be finished with Option A so the client and production don't wait.

## 10. Quick answers

| If you see or want… | Do this |
| --- | --- |
| "Google sign-in did not complete" | Use portal.encoreprimefurniture.com (not an old Railway address) and your @encoreprimefurniture.com account. Still failing → tell Kushal. |
| "You are not on the user list" | Ask Kushal to add you. |
| My quotation isn't in New project | It is won/lost, or already on the desk. Search **Projects** instead. |
| "Not ready yet: Product 2: questions to answer" | Answer that product's questions, or remove it from drawings with a reason. |
| Issue button refuses | Read the reason: open questions, no preview, an AI change waiting (press Accept or Undo), or a failed check. |
| Red bar "Issue and production are locked" | Handle every entry on **Quotation changes**. |
| Email log says **not sent** | Read the reason under it and tell Kushal. |
| Client says they got no code on the portal | Check their email is on **Portal access for this order**, spelt exactly. CC and forwarded emails can't sign in. |
| Client wants a past product again | **Past orders / Remake** (section 2). |
| Need the files | Project page → download the PDF, DXF or combined set. Links open for a few minutes for security; click again if one expired. |
| A new engine version is available | Admins update it. Issued drawings stay on their own version. |

**Never** edit a drawing PDF by hand, send drawings from personal email, or give portal access to an email the client didn't ask for. Ask Kushal when unsure.
