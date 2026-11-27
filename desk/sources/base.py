"""Where orders come from. The desk only ever talks to an OrderSource, never to Odoo directly.
When Odoo is replaced, a new OrderSource is written; nothing else in the desk changes."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SourceLine:
    no: int
    name: str
    description: str
    qty: float = 1
    unit: str = ""
    product_ref: str = ""       # the product's id in the source, used to fetch its photograph
    no_drawing: str = ""        # why this line gets no drawing (see desk/nodrawing.py); "" = it is drawn


@dataclass
class SourceOrder:
    source: str                 # "odoo", "sample", later "encore-erp"
    ref: str                    # the order number in that system, e.g. Q-2026-10-444
    client_name: str
    client_contact: str = ""
    delivery: list = field(default_factory=list)
    salesperson: str = ""
    job_order: str = ""
    date: str = ""
    lines: list = field(default_factory=list)


class OrderSource(Protocol):
    def get_order(self, ref: str) -> SourceOrder: ...


class Sources:
    """Several sources tried in turn (Odoo first, then the sample orders kept for testing)."""
    def __init__(self, *sources):
        self.sources = [s for s in sources if s is not None]

    def get_order(self, ref):
        for s in self.sources:
            try:
                o = s.get_order(ref)
                o._from = s
                return o
            except KeyError:
                continue
        raise KeyError(ref)

    def list_refs(self):
        out = []
        for s in self.sources:
            out += [r for r in getattr(s, "list_refs", lambda: [])() if r not in out]
        return out
