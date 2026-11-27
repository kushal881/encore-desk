"""Which quotation lines get no drawing (decided by Kushal, 27-Nov-2026). Deterministic: no AI.

- Bought-out products: in Odoo the product's route is Buy and not Manufacture (chairs and other traded items).
- Services: the Odoo product type is "service".
- Installation, transport, delivery, freight and the like, recognised from the product name.

These lines are kept apart from the products to draw: they are listed on the project page as "No drawing needed" and
never get questions, drawings or engine requests.
"""
import re

SERVICE_WORDS = re.compile(r"\b(install\w*|transport\w*|deliver\w*|freight|cartage|shipping|shipment|courier|loading|unloading|carriage)\b", re.I)


def classify(name, product_type="", routes=()):
    """-> "" when the line needs a drawing, otherwise the reason it does not."""
    routes = [r.lower() for r in routes or ()]
    if any("buy" in r for r in routes) and not any("manufactur" in r for r in routes):
        return "Bought-out item"
    if (product_type or "").lower() == "service":
        return "Service"
    if SERVICE_WORDS.search(name or ""):
        return "Installation, delivery or transport"
    return ""
