"""Runs every test file in its own Python process and reports one result.

Each test file sets its own temporary database and files folder before it
imports the desk, so the files cannot share one process: run them with this
script, not with a plain `pytest` over the whole folder.

    ENGINES_DIR=/path/to/approved/engines python tests/run_all.py

Exit code 0 when every file passes (skipped files count as passed), 1 otherwise.
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if not os.environ.get("ENGINES_DIR"):
        print("ENGINES_DIR is not set: the drawing tests need a copy of the approved engines.")
        return 1
    os.environ.setdefault("TEST_ENGINES_SRC", os.environ["ENGINES_DIR"])
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    failed = []
    for f in files:
        name = os.path.basename(f)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", f], capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        ok = r.returncode in (0, 5)          # 5 = no tests collected (e.g. a whole file skipped)
        print("%s %-26s %s" % ("PASS" if ok else "FAIL", name, last))
        if not ok:
            failed.append(name)
            print(r.stdout[-4000:])
            print(r.stderr[-2000:])
    print()
    print("All %d test files passed." % len(files) if not failed else "%d of %d test files failed: %s" % (len(failed), len(files), ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
