#!/usr/bin/env python3
"""Apply the proposed compactionId identity to a tokscale checkout.

Inserts one `.or_else` ahead of the `seq:` fallback added by #1173. The seq
fallback stays, so nothing that relies on it regresses; it just stops being
the first thing an idless row reaches.

    python3 proposed.patch.py /path/to/tokscale-checkout
"""
from __future__ import annotations
import sys
from pathlib import Path

BEFORE = '''                    .map(|id| format!("msg:{id}"))
                    .or_else(|| {
                        value
                            .get("seq")
'''

AFTER = '''                    .map(|id| format!("msg:{id}"))
                    // A `compaction/summary` has no `message.id`, but it does
                    // carry `data.compactionId` -- a per-call uuid that a fork
                    // copies verbatim along with the rest of the prefix, the
                    // same property `msg:{id}` relies on. Unlike `seq`, which
                    // is dense from zero in every file, it is unique across
                    // unrelated sessions, so the copied summary still collapses
                    // and two genuinely separate summarize calls cannot.
                    .or_else(|| {
                        value
                            .pointer("/data/compactionId")
                            .and_then(Value::as_str)
                            .map(str::trim)
                            .filter(|id| !id.is_empty())
                            .map(|id| format!("cmp:{id}"))
                    })
                    .or_else(|| {
                        value
                            .get("seq")
'''


def main() -> int:
    root = Path(sys.argv[1])
    f = root / "crates/tokscale-core/src/sessions/dsh.rs"
    s = f.read_text()
    if "cmp:{id}" in s:
        print("already patched")
        return 0
    if s.count(BEFORE) != 1:
        print(f"anchor not found exactly once ({s.count(BEFORE)}); refusing")
        return 1
    f.write_text(s.replace(BEFORE, AFTER))
    print(f"patched {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
