"""Fetching the approved engines by version number, with GitHub's answers faked from a local copy."""
import os, sys, io, tarfile, tempfile
home = tempfile.mkdtemp()
os.environ.update(ENGINES_HOME=home, ENGINES_KEY="read-only-key", ENGINES_DIR="")
SRC = os.environ.get("TEST_ENGINES_SRC", "/home/claude/approved_engines")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from desk import engines


class R:
    def __init__(self, js=None, content=b""): self._js, self.content = js, content
    def json(self): return self._js
    def raise_for_status(self): pass


def test_fetch():
    calls = []
    def get(url, headers=None, **kw):
        calls.append(url); assert headers["Authorization"] == "Bearer read-only-key"
        if url.endswith("/tags?per_page=100"):
            return R([{"name": "v1"}, {"name": "v10"}, {"name": "v9"}, {"name": "rejected/abc"}])
        assert url.endswith("/tarball/v10"), url
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(os.path.join(SRC, "tool"), arcname="kushal881-encore-approved-abc123/tool")
            tf.add(os.path.join(SRC, "templates"), arcname="kushal881-encore-approved-abc123/templates")
        return R(content=buf.getvalue())
    httpx.get = get
    assert engines.fetch_latest() == "v10" and engines.version() == "v10"
    assert os.path.exists(os.path.join(home, "v10", "tool", "encore_drawing.py")) and engines.ENGINES_DIR == os.path.join(home, "v10")
    n = len(calls)
    assert engines.fetch_latest() == "v10" and len(calls) == n + 1          # second time: only the tag list, no download


if __name__ == "__main__":
    test_fetch(); print("engine fetch test passes")
