#!/usr/bin/env python3
"""Red/green check of kelviq/tare `ccaudit.py` against a synthetic corpus.

Generates the fixture (gen_fixture.py), runs ccaudit over it, and compares what
it reports with the manifest. Four cases:

  A  I-3   a session replayed verbatim into a second file (resume) must not
           be counted twice
  B  I-2/4 sidechain records carry a running total; the final record wins
  C  I-11  a model outside MODEL_RATES must be loud, not weigh 0.00 silently
  D  ---   tool events must respect --days like usage records do

    ./check_tare.py --tare-dir /path/to/tare        # an existing checkout
    ./check_tare.py                                  # clones kelviq/tare at the pinned commit

Stdlib only. Exit 0 when every case passes, 1 otherwise. Never touches ~/.claude.
Measured 2026-09-04 against 03159f2 (ccaudit 0.2.0): A FAIL, B PASS, C FAIL, D FAIL.
Filed as https://github.com/kelviq/tare/issues/8.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = "03159f2"
REPO = "https://github.com/kelviq/tare.git"


def run(cmd, **kw):
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def ccaudit(tare_dir: Path, home: Path, *args):
    script = tare_dir / "skills" / "tare" / "ccaudit.py"
    r = run([sys.executable, str(script), "--dir", str(home / ".claude" / "projects"),
             "--tz", "0", *args])
    if r.returncode not in (0,):
        sys.stderr.write(r.stderr)
    return r.stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tare-dir", help="existing kelviq/tare checkout (default: clone)")
    p.add_argument("--commit", default=PINNED, help=f"commit to check out when cloning (default {PINNED})")
    a = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="tare-check-"))
    if a.tare_dir:
        tare_dir = Path(a.tare_dir).resolve()
    else:
        tare_dir = tmp / "tare"
        r = run(["git", "clone", "--quiet", REPO, str(tare_dir)])
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 2
        run(["git", "-C", str(tare_dir), "checkout", "--quiet", a.commit])
    sha = run(["git", "-C", str(tare_dir), "rev-parse", "--short", "HEAD"]).stdout.strip() or "?"

    home = tmp / "home"
    r = run([sys.executable, str(HERE / "gen_fixture.py"), str(home)])
    if r.returncode:
        print(r.stderr, file=sys.stderr)
        return 2
    m = json.loads((home / "manifest.json").read_text())

    # one 30-day pass with a CSV for per-request rows, one 1-day pass for tool scoping
    csv_path = tmp / "usage.csv"
    out30 = ccaudit(tare_dir, home, "--days", "30", "--doctor", "--csv", str(csv_path))
    out1 = ccaudit(tare_dir, home, "--days", "1", "--by", "tool")

    rows = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8")))
    by_session = defaultdict(list)
    for r in rows:
        by_session[r["session"]].append(r)

    def sess(suffix):
        return next((v for k, v in by_session.items() if k.endswith(suffix)), [])

    results = []

    # A — I-3: 50 distinct messages, not 90
    got = len(sess("-a"))
    want = m["messages"]["case_A_distinct_message_ids"]
    results.append(("A", "I-3 cross-file replay", f"{got} requests", f"{want}", got == want))

    # B — I-2/I-4: sum of final output_tokens on the sidechain session
    got = sum(int(r["output_tokens"]) for r in sess("-c"))
    want = m["tokens"]["case_B_output_correct_last_wins"]
    results.append(("B", "I-2/I-4 sidechain running total", f"{got} output tokens", f"{want}", got == want))

    # C — I-11: the unpriced model must either carry weight or raise a finding that names it
    nova = [r for r in rows if "nova" in r["model"]]
    weight = sum(float(r["weight"]) for r in nova)
    findings = out30.split("FINDINGS", 1)[-1]
    # a finding's detail is word-wrapped under its [!]/[~] header, so look at
    # each header-plus-detail block, not at single lines
    blocks = re.split(r"\n(?=\[)", findings)
    loud = any(b.startswith(("[!]", "[~]")) and "nova" in b for b in blocks)
    results.append(("C", "I-11 unpriced model is loud", f"weight={weight:.2f}, finding={'yes' if loud else 'no'}",
                    "weight>0 or a [!]/[~] finding naming it", weight > 0 or loud))

    # D — tool events scoped to --days
    mm = re.search(r"(\d+) tool calls", out1)
    got = int(mm.group(1)) if mm else -1
    want = m["tool_results"]["case_D_in_last_24h"]
    results.append(("D", "tool events respect --days", f"{got} tool calls at --days 1", f"{want}", got == want))

    print(f"kelviq/tare @ {sha} · fixture seed 20260904 · {len(rows)} requests parsed\n")
    print(f"{'case':<5}{'invariant':<34}{'reported':<40}{'expected':<44}verdict")
    print("-" * 130)
    for c, inv, got, want, ok in results:
        print(f"{c:<5}{inv:<34}{got:<40}{want:<44}{'PASS' if ok else 'FAIL'}")
    bad = [c for c, *_, ok in results if not ok]
    print(f"\n{len(results) - len(bad)} pass, {len(bad)} fail" + (f" ({', '.join(bad)})" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
