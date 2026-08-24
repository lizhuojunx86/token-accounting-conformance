#!/usr/bin/env python3
"""How much of tokscale's DSH dedup key is actually discriminating?

Reads a DSH sessions root and reports, for every usage-bearing row the parser
would key, which identity branch it takes and whether the resulting key is
shared by rows in different files.

Branches, from crates/tokscale-core/src/sessions/dsh.rs at main (1c83f8d):

    msg:{data.message.id}   when present and non-empty
    seq:{seq}               otherwise, when seq is an integer   <- added by #1173
    sid:{session id}        otherwise

The key is then
    dsh:{kind}{identity}:{time}:{provider}:{model}:{i}:{o}:{cr}:{cw}:{reasoning}
with kind = "summary:" for compaction/summary and "" for assistant/message.

Read-only. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

USAGE_TYPES = {"assistant/message", "compaction/summary"}


def tokens_from_usage(u: dict) -> tuple:
    def i(k):
        v = u.get(k)
        return v if isinstance(v, int) else 0
    out = max(i("outputTokens"), 0)
    rea = max(i("reasoningTokens"), 0)
    return (i("inputTokens"), max(out - rea, 0), i("cacheReadTokens"),
            i("cacheWriteTokens"), rea)


def rows(root: Path):
    for path in sorted(root.rglob("session.jsonl")):
        sid_from_path = path.parent.name
        for line in path.open(encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") not in USAGE_TYPES:
                continue
            data = e.get("data") or {}
            usage = data.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = e.get("time")
            if not isinstance(ts, int) or ts <= 0:
                continue
            src = ((data.get("message") or {}).get("source")) or {}
            mid = ((data.get("message") or {}).get("id"))
            mid = mid.strip() if isinstance(mid, str) else None
            seq = e.get("seq")
            if mid:
                identity, branch = f"msg:{mid}", "msg"
            elif isinstance(seq, int):
                identity, branch = f"seq:{seq}", "seq"
            else:
                identity, branch = f"sid:{sid_from_path}", "sid"
            kind = "summary:" if e["type"] == "compaction/summary" else ""
            key = "dsh:{}{}:{}:{}:{}:{}:{}:{}:{}:{}".format(
                kind, identity, ts,
                src.get("provider") or "unknown", src.get("model") or "unknown",
                *tokens_from_usage(usage))
            yield path, branch, key, seq, ts, e["type"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    root = Path(args.root).expanduser()

    branches = Counter()
    by_key: dict[str, set] = defaultdict(set)
    seq_ts: dict[int, set] = defaultdict(set)
    ts_all = Counter()
    n = 0

    for path, branch, key, seq, ts, etype in rows(root):
        n += 1
        branches[f"{etype} -> {branch}"] += 1
        by_key[key].add(str(path))
        if isinstance(seq, int):
            seq_ts[seq].add(str(path))
        ts_all[ts] += 1

    print(f"root                  {root}")
    print(f"usage-bearing rows    {n}")
    print("\nidentity branch taken")
    for k, v in sorted(branches.items()):
        print(f"  {k:<40} {v:>6}")

    cross = {k: v for k, v in by_key.items() if len(v) > 1}
    print(f"\nkeys shared across files   {len(cross)}")
    for k, v in list(cross.items())[:5]:
        print(f"  {k}\n    {sorted(v)}")

    shared_seq = {s: v for s, v in seq_ts.items() if len(v) > 1}
    print(f"\nseq values appearing in >1 file  {len(shared_seq)} of {len(seq_ts)}")
    dup_ts = sum(1 for c in ts_all.values() if c > 1)
    print(f"timestamps carrying >1 row       {dup_ts} of {len(ts_all)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
