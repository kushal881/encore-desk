# encore-desk

Encore Prime drawing desk and client portal. The first pieces of Encore's own ERP.

- **Drawing desk** (staff): reads an order, asks what is missing, runs the approved engines, issues drawings.
- **Client portal** (later): client login, drawing approval, documents and payments.
- **Connector**: the only part that talks to Odoo. Replaced when Odoo is replaced; nothing else changes.

Engines are never stored or edited here. They are fetched read-only, by version number, from the approved engines repository.

Status: started 26 Nov 2026. Nothing deployed yet.
