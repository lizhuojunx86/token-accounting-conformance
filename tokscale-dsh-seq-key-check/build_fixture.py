#!/usr/bin/env python3
"""Build the two-leg fixture for tokscale's DSH idless-row dedup key.

Both legs turn on the same question: what identifies a `compaction/summary`,
which carries no `data.message.id`.

  leg A  two UNRELATED sessions whose summaries agree on seq, time, routing and
         every usage bucket, and differ only in their per-call ids,
         `data.compactionId` and `data.sourceCommandId`.
         Under the `seq:` fallback (tokscale#1173) the two keys are identical
         and the cross-file pass drops one billed call.

  leg B  a parent and a fork whose header lost `seedLength` -- the case the
         `seq:` fallback was added for. The child's copied prefix repeats the
         parent's summary verbatim, same `compactionId` included, and the two
         must collapse to one.

A key built on `compactionId` satisfies both. A key built on `seq` satisfies
only B. That is the whole of the fixture.

Every number is distinct and non-round, so a fold landing on the wrong total
names its own mistake in the arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "fixture" / "sessions" / "--fixture--"

T0 = 1_780_000_000_000
PROVIDER = "fixture-provider"
MODEL = "fixture-model"


def usage(i, o, cr, cw):
    return {"inputTokens": i, "outputTokens": o,
            "cacheReadTokens": cr, "cacheWriteTokens": cw}


def total(u):
    return u["inputTokens"] + u["outputTokens"] + u["cacheReadTokens"] + u["cacheWriteTokens"]


# --- the six usage values, each used exactly where the comment says ---------
U_A_WORK = usage(11, 2, 110, 1)        # 124    alpha's own reply
U_B_WORK = usage(13, 3, 130, 1)        # 147    bravo's own reply
U_COLLIDE = usage(307, 31, 3070, 7)    # 3415   BOTH leg-A summaries, by construction
U_C_WORK = usage(17, 5, 170, 2)        # 194    charlie's reply, copied into delta
U_C_SUM = usage(503, 53, 5030, 3)      # 5589   charlie's summary, copied into delta
U_D_WORK = usage(19, 7, 190, 2)        # 218    delta's own reply


def header(sid, *, parent=None, seed=None):
    data = {"id": sid, "cwd": "/fixture", "createdAt": T0}
    if parent is not None:
        data["parentSession"] = parent
    if seed is not None:
        data["seedLength"] = seed
    return {"type": "session", **data}


def ev(seq, type_, data, t=None):
    return {"type": type_, "seq": seq, "time": T0 + (seq if t is None else t), "data": data}


def req_header(seq):
    return ev(seq, "request/header",
              {"header": {"config": {"provider": PROVIDER, "model": MODEL}}})


def reply(seq, msg_id, u, turn):
    return ev(seq, "assistant/message", {
        "turn": turn, "step": 0,
        "message": {"id": msg_id, "source": {"provider": PROVIDER, "model": MODEL}},
        "usage": u,
    })


def summary(seq, compaction_id, u, *, t=None):
    return ev(seq, "compaction/summary", {
        "compactionId": compaction_id,
        "sourceCommandId": f"cmd-{compaction_id}",
        "provider": PROVIDER, "model": MODEL,
        "shadowedTokenCount": 1234,
        "summary": "fixture summary text",
        "usage": u,
    }, t=t)


# --- leg A: two unrelated sessions, colliding on everything but compactionId -
# Same seq (7) and, deliberately, the same millisecond: `time` is passed
# explicitly so both land on T0+7 rather than on their own offsets.
ALPHA = [
    header("session-alpha"),
    req_header(1),
    ev(2, "user/message", {"turn": 0}),
    reply(3, "msg-alpha", U_A_WORK, 0),
    ev(6, "compaction/start", {"compactionId": "cmp-alpha", "turn": None}),
    summary(7, "cmp-alpha", U_COLLIDE, t=7),
    ev(8, "compaction/end", {"compactionId": "cmp-alpha", "turn": None}),
]

BRAVO = [
    header("session-bravo"),
    req_header(1),
    ev(2, "user/message", {"turn": 0}),
    reply(3, "msg-bravo", U_B_WORK, 0),
    ev(6, "compaction/start", {"compactionId": "cmp-bravo", "turn": None}),
    summary(7, "cmp-bravo", U_COLLIDE, t=7),
    ev(8, "compaction/end", {"compactionId": "cmp-bravo", "turn": None}),
]

# --- leg B: parent, and a fork whose header lost seedLength -----------------
CHARLIE_PREFIX = [
    req_header(1),
    ev(2, "user/message", {"turn": 0}),
    reply(3, "msg-charlie", U_C_WORK, 0),
    ev(4, "compaction/start", {"compactionId": "cmp-charlie", "turn": None}),
    summary(5, "cmp-charlie", U_C_SUM),
]
CHARLIE = [header("session-charlie"), *CHARLIE_PREFIX]

# The fork copies the prefix verbatim -- same seq, same time, same usage, same
# message.id, same compactionId -- and records no seedLength. This is exactly
# the shape tokscale#1173 added the `seq:` fallback for.
DELTA = [
    header("session-delta", parent="session-charlie"),
    *CHARLIE_PREFIX,
    reply(6, "msg-delta", U_D_WORK, 1),
]

SESSIONS_SPEC = {
    "session-alpha": ALPHA,
    "session-bravo": BRAVO,
    "session-charlie": CHARLIE,
    "session-delta": DELTA,
}


def expectations() -> dict:
    """Derive the correct totals by arithmetic, not by running anything."""
    billed = [
        ("alpha reply", U_A_WORK),
        ("alpha summary", U_COLLIDE),
        ("bravo reply", U_B_WORK),
        ("bravo summary", U_COLLIDE),      # a SECOND billed call, not a copy
        ("charlie reply", U_C_WORK),       # delta's copy collapses onto it
        ("charlie summary", U_C_SUM),      # delta's copy collapses onto it
        ("delta reply", U_D_WORK),
    ]
    correct = {
        "totalInput": sum(u["inputTokens"] for _, u in billed),
        "totalOutput": sum(u["outputTokens"] for _, u in billed),
        "totalCacheRead": sum(u["cacheReadTokens"] for _, u in billed),
        "totalCacheWrite": sum(u["cacheWriteTokens"] for _, u in billed),
        "totalMessages": len(billed),
    }
    # What a `seq:`-keyed fold produces: bravo's summary is dropped as a
    # duplicate of alpha's.
    seq_keyed = dict(correct)
    for field, key in (("totalInput", "inputTokens"), ("totalOutput", "outputTokens"),
                       ("totalCacheRead", "cacheReadTokens"),
                       ("totalCacheWrite", "cacheWriteTokens")):
        seq_keyed[field] -= U_COLLIDE[key]
    seq_keyed["totalMessages"] -= 1
    return {"correct": correct, "seq_keyed": seq_keyed,
            "dropped_call_total": total(U_COLLIDE)}


def main() -> int:
    for sid, events in SESSIONS_SPEC.items():
        d = SESSIONS / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.jsonl").write_text(
            "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events),
            encoding="utf-8")
    exp = expectations()
    (HERE / "fixture" / "expected.json").write_text(
        json.dumps(exp, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(exp, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
