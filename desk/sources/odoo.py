"""Orders, clients and product photographs read from Odoo. Read-only: this file never writes to Odoo.
Settings (on the server): ODOO_URL, ODOO_DB, ODOO_USER (the login e-mail of the user the key belongs to), ODOO_KEY (an API key).
Uses Odoo's newer JSON-2 interface when the database has it (Odoo 19, Online 18.4 onwards) and the older one otherwise."""
import os, re, base64, time
import httpx
from .base import SourceOrder, SourceLine


class OdooError(RuntimeError):
    pass


class OdooSource:
    name = "odoo"

    def __init__(self, url=None, db=None, user=None, key=None, client=None):
        self.url = (url or os.environ.get("ODOO_URL", "")).strip().rstrip("/")
        if self.url and not self.url.startswith("http"):
            self.url = "https://" + self.url
        self.db = db or os.environ.get("ODOO_DB", "")
        self.user = user or os.environ.get("ODOO_USER", "")
        self.key = key or os.environ.get("ODOO_KEY", "")
        self.http = client or httpx.Client(timeout=30)
        self.mode, self.uid, self._status = None, None, (0, "")

    @property
    def configured(self):
        return bool(self.url and self.key)

    # ---- transport -------------------------------------------------------------------------------------------------
    def _json2(self, model, method, kw):
        h = {"Authorization": "bearer " + self.key}
        if self.db:
            h["X-Odoo-Database"] = self.db
        return self.http.post("%s/json/2/%s/%s" % (self.url, model, method), headers=h, json=kw)

    def _rpc(self, service, method, args):
        r = self.http.post(self.url + "/jsonrpc", json=dict(jsonrpc="2.0", method="call", id=1, params=dict(service=service, method=method, args=args)))
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            raise OdooError(str((d["error"].get("data") or {}).get("message") or d["error"].get("message"))[:300])
        return d.get("result")

    def call(self, model, method, **kw):
        if not self.configured:
            raise OdooError("Odoo is not set up on this server")
        try:
            if self.mode in (None, "json2"):
                r = self._json2(model, method, kw)
                if r.status_code == 404 and self.mode is None:
                    self.mode = "rpc"                              # an Odoo without the newer interface
                elif r.status_code in (401, 403):
                    raise OdooError("Odoo refused the key (it may have expired, or the user cannot read this)")
                elif r.status_code >= 400:
                    raise OdooError("Odoo answered %d: %s" % (r.status_code, r.text[:200]))
                else:
                    self.mode = "json2"
                    return r.json()
            if not (self.db and self.user):
                raise OdooError("This Odoo needs ODOO_DB and ODOO_USER as well as the key")
            if not self.uid:
                self.uid = self._rpc("common", "authenticate", [self.db, self.user, self.key, {}])
                if not self.uid:
                    raise OdooError("Odoo refused the user or key")
            kw = dict(kw)
            args = [kw.pop("ids")] if "ids" in kw else []
            return self._rpc("object", "execute_kw", [self.db, self.uid, self.key, model, method, args, kw])
        except httpx.HTTPError as e:
            raise OdooError("Could not reach Odoo: %s" % str(e)[:150])

    # ---- what the desk asks for ------------------------------------------------------------------------------------
    def get_order(self, ref):
        rows = self.call("sale.order", "search_read", domain=[["name", "=", ref.strip()]], limit=1,
                         fields=["name", "partner_id", "partner_shipping_id", "user_id", "date_order", "order_line", "client_order_ref"])
        if not rows:
            raise KeyError(ref)
        o = rows[0]
        partner = self.call("res.partner", "read", ids=[o["partner_id"][0]], fields=["name", "commercial_company_name", "parent_id"])[0]
        company = partner.get("commercial_company_name") or (partner["parent_id"][1] if partner.get("parent_id") else partner["name"])
        contact = partner["name"] if partner["name"] != company else ""
        delivery = []
        if o.get("partner_shipping_id"):
            a = self.call("res.partner", "read", ids=[o["partner_shipping_id"][0]], fields=["street", "street2", "city", "zip", "state_id"])[0]
            first = ", ".join(x for x in (a.get("street"), a.get("street2")) if x)
            second = " ".join(x for x in (a.get("city"), a.get("zip")) if x) + (", " + a["state_id"][1].split(" (")[0] if a.get("state_id") else "")
            delivery = [x for x in (first, second.strip(", ")) if x]
        lines, no = [], 0
        raw = self.call("sale.order.line", "read", ids=o["order_line"], fields=["sequence", "name", "product_id", "product_uom_qty", "display_type"]) if o["order_line"] else []
        for l in sorted(raw, key=lambda x: (x.get("sequence") or 0, x["id"])):
            if l.get("display_type") or not l.get("product_id"):
                continue                                           # section titles and notes are not products
            no += 1
            text = (l.get("name") or "").strip()
            text = re.sub(r"^\[[^\]]+\]\s*", "", text)             # an internal reference in front of the name
            head, _, rest = text.partition("\n")
            lines.append(SourceLine(no=no, name=head.strip() or l["product_id"][1], description=rest.strip(), qty=l.get("product_uom_qty") or 1, product_ref=str(l["product_id"][0])))
        return SourceOrder(source="odoo", ref=o["name"], client_name=company, client_contact=contact, delivery=delivery,
                           salesperson=o["user_id"][1] if o.get("user_id") else "", job_order="", date=str(o.get("date_order") or "")[:10], lines=lines)

    def list_refs(self, limit=15):
        try:
            rows = self.call("sale.order", "search_read", domain=[["state", "in", ["draft", "sent", "sale"]]], fields=["name"], limit=limit, order="id desc")
        except OdooError:
            return []
        return [r["name"] for r in rows]

    def product_image(self, product_ref):
        """-> the product's photograph as bytes, or None if Odoo has none."""
        rows = self.call("product.product", "read", ids=[int(product_ref)], fields=["image_1024"])
        data = rows[0].get("image_1024") if rows else None
        return base64.b64decode(data) if data else None

    def status(self):
        """A short line for the administrator. Checked at most once a minute."""
        if time.time() - self._status[0] > 60:
            try:
                n = self.call("sale.order", "search_count", domain=[])
                self._status = (time.time(), "Connected. %d quotations and orders can be read." % n)
            except OdooError as e:
                self._status = (time.time(), "Not working: %s" % e)
        return self._status[1]
