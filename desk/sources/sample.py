"""Orders read from files in samples/. Used for building and testing before Odoo is connected."""
import json, os
from .base import SourceOrder, SourceLine

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "samples")


class SampleSource:
    def get_order(self, ref: str) -> SourceOrder:
        path = os.path.join(HERE, os.path.basename(ref) + ".json")
        if not os.path.exists(path):
            raise KeyError(ref)
        d = json.load(open(path, encoding="utf-8"))
        return SourceOrder(source=d["source"], ref=d["ref"], client_name=d["client"]["name"], client_contact=d["client"].get("contact", ""),
                           delivery=d.get("delivery", []), salesperson=d.get("salesperson", ""), job_order=d.get("job_order", ""), date=d.get("date", ""),
                           lines=[SourceLine(**l) for l in d["lines"]])

    def list_refs(self):
        return sorted(f[:-5] for f in os.listdir(HERE) if f.endswith(".json"))
