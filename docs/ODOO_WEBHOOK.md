# Odoo → desk: quotation changes (decision 2)

When a salesperson changes a quotation in Odoo, Odoo tells the desk at once. The desk then reads the quotation again
(read-only) and lists every difference in the project's **Quotation changes** inbox. Safety nets: the desk also checks
when a project is opened, when someone presses **Check Odoo now**, and once a night.

Do this once for **staging** and once for **live**, with each one's own address and token.

## 1. Make a secret token (Railway)
1. Railway → project **worthy-creation** → service **web** → **Variables**.
2. Add `ODOO_WEBHOOK_TOKEN` = a long random secret: at least 32 letters and digits from a password generator. Use a
   different one for staging and live. Don't send it by email or chat.
3. Railway redeploys. Until this variable is set, the desk refuses every webhook call (it answers 503).

## 2. The automation rule (Odoo)
1. In Odoo, turn on **developer mode**: Settings → scroll to the bottom → **Activate the developer mode**.
2. Settings → **Technical** → Automation → **Automation Rules** → **New**.
3. Fill in:
   - **Name:** `Drawing desk: quotation changed`
   - **Model:** `Sales Order`
   - **Trigger:** `On create and edit` (in some versions: *On Save*)
   - **When updating** / **Trigger Fields:** pick `Order Lines`, `Status`. Add `Delivery Date` and your Job Order No
     field too, so the production deadline stays current.
   - **Apply on:** leave empty (all sales orders). The desk ignores quotations that are not projects.
4. Under **Actions To Do** → **Add an action** → type **Send Webhook Notification**:
   - **URL:**
     - live: `https://design.encoreprimefurniture.com/webhooks/odoo?token=<the live ODOO_WEBHOOK_TOKEN>`
     - staging: `https://<staging address>/webhooks/odoo?token=<the staging ODOO_WEBHOOK_TOKEN>`
   - **Fields:** add `Order Reference` (`name`). That's all the desk needs.
5. **Save.**
6. Test it: open a TEST quotation that is a project on the desk. Change a quantity and save. In the desk, that project's
   **Quotation changes** tab should show "Qty changed" within a few seconds. History shows "Odoo version N found · webhook".

If the test does nothing: in Odoo, Settings → Technical → **Logging** shows the webhook call and its answer.
- `401` = the token in the URL is wrong.
- `503` = the desk has no `ODOO_WEBHOOK_TOKEN` yet.
- `200` with `"project": false` = that quotation isn't a project on the desk.

## 3. The nightly check (Railway cron)
A safety net for changes the rule can miss (for example, lines changed by an import).
1. Railway → project **worthy-creation** → **New** → **Empty service** → name it `nightly-odoo-check`.
2. Settings → Source → **Docker image**: `curlimages/curl:8.11.1`.
3. Settings → **Custom start command**:
   `curl -fsS -X POST "https://design.encoreprimefurniture.com/webhooks/odoo/nightly?token=${ODOO_WEBHOOK_TOKEN}"`
4. Variables → `ODOO_WEBHOOK_TOKEN` = `${{web.ODOO_WEBHOOK_TOKEN}}`, a reference to the web service's own value.
5. Settings → **Cron Schedule**: `30 20 * * *` (02:00 India time).
6. Its log should show `{"ok":true,"checked":N,"failed":[]}` each night.

## 4. Job order number (decision 3)
Odoo's **Delivery Date** (`commitment_date`) is read by itself. For **Job Order No**, add the field's technical name to
Railway as `ODOO_JOB_ORDER_FIELD`. To find it: in developer mode, open a sales order and hover over the Job Order No
field. The tooltip shows *Field: x_…* (e.g. `x_studio_job_order_no`).
