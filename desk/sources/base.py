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
