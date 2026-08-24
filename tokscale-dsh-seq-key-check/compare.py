#!/usr/bin/env python3
"""Compare a tokscale run over the fixture against both predicted outcomes.

The fixture is built so the two candidate dedup keys give different totals:

  compactionId-keyed   both legs correct
  seq-keyed            leg B correct, leg A drops one billed summarize call

So the run does not merely pass or fail. It names which key the binary used.
"""
from __future__ import annotations
import argparse, json, sys

FIELDS = ("totalInput", "totalOutput", "totalCacheRead",
          "totalCacheWrite", "totalMessages")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--expected", required=True)
    a = ap.parse_args()

    got = json.load(open(a.result))
    exp = json.load(open(a.expected))
    correct, seq_keyed = exp["correct"], exp["seq_keyed"]

    actual = {f: got.get(f) for f in FIELDS}
    w = max(len(f) for f in FIELDS)
    print(f"\n{'field':<{w}}  {'measured':>10} {'compactionId':>13} {'seq':>10}")
    for f in FIELDS:
        print(f"{f:<{w}}  {str(actual[f]):>10} {correct[f]:>13} {seq_keyed[f]:>10}")

    if actual == correct:
        print("\nverdict: compactionId-equivalent — both legs correct")
        return 0
    if actual == seq_keyed:
        print(f"\nverdict: seq-keyed — leg A dropped one billed summarize call, "
              f"{exp['dropped_call_total']} tokens")
        print("         leg B (the fork the fallback exists for) is correct either way")
        return 1
    print("\nverdict: neither — the fixture is exercising something else, "
          "do not quote either column")
    return 2


if __name__ == "__main__":
    sys.exit(main())
