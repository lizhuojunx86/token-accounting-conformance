#!/usr/bin/env python3
"""Print each DSH session's header identity fields.

The question this answers: when a fork loses `seedLength` -- the case #1173's
`seq:` fallback exists for -- does the header still carry a lineage field that
could scope the key instead? Read-only, stdlib only.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

INTERESTING = ("id", "sessionId", "parentSession", "parentSessionId",
               "seedLength", "origin", "cwd", "workspace", "createdAt", "title")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    for path in sorted(Path(a.root).expanduser().rglob("session.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
        try:
            e = json.loads(first)
        except Exception:
            print(f"{path.parent.name}  <unparseable first line>")
            continue
        print(f"\n=== {path.parent.name}")
        print(f"    first event type: {e.get('type')}  seq={e.get('seq')}")
        data = e.get("data") or {}
        blob = {**{k: v for k, v in e.items() if k not in ("data",)}, **data}
        for k in INTERESTING:
            if k in blob:
                print(f"    {k:<18} {json.dumps(blob[k])[:120]}")
        extra = [k for k in data if k not in INTERESTING]
        if extra:
            print(f"    (other data keys) {extra}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
