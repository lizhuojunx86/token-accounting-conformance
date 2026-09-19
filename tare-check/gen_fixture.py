#!/usr/bin/env python3
"""Synthetic ~/.claude/projects fixture to measure kelviq/tare ccaudit.py
against CONFORMANCE.md invariants I-2/I-4 (should pass), I-3 (cross-file
replay), I-11 (unpriced model), and window scoping of tool events.

Writes <home>/.claude/projects/... and <home>/manifest.json (ground truth).
Stdlib only. Touches nothing outside <home>.
"""
import json, os, random, sys
from datetime import datetime, timedelta, timezone

home = sys.argv[1]
rng = random.Random(20260904)
root = os.path.join(home, ".claude", "projects")
NOW = datetime.now(timezone.utc)

def hx(n): return "".join(rng.choice("0123456789abcdef") for _ in range(n))
def mid(): return "msg_01" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") for _ in range(22))
def iso(t): return t.isoformat().replace("+00:00", "Z")

def assistant_recs(session, ts, model, usage_list, sidechain=False, tool=False):
    """One assistant message emitted as len(usage_list) records sharing id+requestId.
    usage_list lets sidechain records carry a running total (I-2/I-4 shape)."""
    m, rid = mid(), "req_01" + hx(16)
    recs, tool_id = [], None
    for i, u in enumerate(usage_list):
        blk = {"type": "text", "text": "synthetic"}
        if tool and i == len(usage_list) - 1:
            tool_id = "toolu_01" + hx(16)
            blk = {"type": "tool_use", "id": tool_id, "name": "Read",
                   "input": {"file_path": "/repo/big_file.py"}}
        r = {"type": "assistant", "uuid": hx(32), "sessionId": session,
             "requestId": rid, "timestamp": iso(ts + timedelta(milliseconds=200 * i)),
             "message": {"id": m, "role": "assistant", "model": model,
                         "content": [blk], "usage": dict(u)}}
        if sidechain:
            r["isSidechain"] = True
        recs.append(r)
    return recs, tool_id, m

def tool_result(session, ts, tool_id, size=4000):
    return {"type": "user", "uuid": hx(32), "sessionId": session, "timestamp": iso(ts),
            "message": {"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": tool_id, "content": "x" * size}]}}

def usage(out=None):
    return {"input_tokens": rng.randint(1, 40), "output_tokens": out if out is not None else rng.randint(20, 4000),
            "cache_creation_input_tokens": rng.randint(0, 30000),
            "cache_read_input_tokens": rng.randint(0, 90000)}

manifest = {"messages": {}, "tokens": {}, "tool_results": {}}
def note(section, key, val): manifest[section][key] = val

def write(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in recs: fh.write(json.dumps(r) + "\n")

# ---------------------------------------------------------------- Case A: I-3
# Session A: 40 messages in A.jsonl (2 days ago). Then a resume replays all 40
# records verbatim into B.jsonl and appends 10 new messages. Ground truth = 50.
proj = "-Users-dev-alpha"
sA = hx(8) + "-a"
t = NOW - timedelta(days=2)
recsA, totA = [], {k: 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
for _ in range(40):
    t += timedelta(seconds=30)
    u = usage()
    rs, _, _ = assistant_recs(sA, t, "claude-sonnet-4-6-20260219", [u, u, u])  # 3 blocks, identical usage (I-1 shape)
    recsA += rs
    for k in totA: totA[k] += u[k]
write(os.path.join(root, proj, sA + ".jsonl"), recsA)
sB = hx(8) + "-b"
recsB = [dict(r) for r in recsA]          # verbatim replay, same requestId / message.id
totB = dict(totA)
for _ in range(10):
    t += timedelta(seconds=30)
    u = usage()
    rs, _, _ = assistant_recs(sA, t, "claude-sonnet-4-6-20260219", [u, u])
    recsB += rs
    for k in totB: totB[k] += u[k]
write(os.path.join(root, proj, sB + ".jsonl"), recsB)
note("messages", "case_A_distinct_message_ids", 50)
note("messages", "case_A_if_per_file_only", 90)
note("tokens", "case_A_output_correct", totB["output_tokens"])
note("tokens", "case_A_output_if_per_file_only", totA["output_tokens"] + totB["output_tokens"])

# ---------------------------------------------------------------- Case B: I-2/I-4
# Subagent transcript: 20 messages, each 3 records carrying a RUNNING TOTAL of
# output_tokens (partial, partial, final). Correct = sum of finals.
proj2 = "-Users-dev-beta"
sC = hx(8) + "-c"
t = NOW - timedelta(days=1)
recsC, final_out, first_out = [], 0, 0
for _ in range(20):
    t += timedelta(seconds=30)
    fin = rng.randint(500, 4000)
    base = usage(out=fin)
    seq = [dict(base, output_tokens=1), dict(base, output_tokens=fin // 2), dict(base, output_tokens=fin)]
    rs, _, _ = assistant_recs(sC, t, "claude-opus-4-8-20260514", seq, sidechain=True)
    recsC += rs
    final_out += fin; first_out += 1
write(os.path.join(root, proj2, sC, "subagents", "agent-" + hx(17) + ".jsonl"), recsC)
note("tokens", "case_B_output_correct_last_wins", final_out)
note("tokens", "case_B_output_if_keep_first", first_out)
# what summing every snapshot (1 + fin//2 + fin per message) would report
from collections import defaultdict
grp = defaultdict(list)
for r in recsC: grp[r["message"]["id"]].append(r["message"]["usage"]["output_tokens"])
note("tokens", "case_B_output_if_sum_snapshots", sum(sum(v) for v in grp.values()))

# ---------------------------------------------------------------- Case C: I-11
# 15 messages on a model family that matches none of MODEL_RATES' substrings.
proj3 = "-Users-dev-gamma"
sD = hx(8) + "-d"
t = NOW - timedelta(hours=6)
recsD, unpriced_out = [], 0
for _ in range(15):
    t += timedelta(seconds=30)
    u = usage(out=3000)
    rs, _, _ = assistant_recs(sD, t, "claude-nova-1-20260901", [u])
    recsD += rs; unpriced_out += 3000
write(os.path.join(root, proj3, sD + ".jsonl"), recsD)
note("tokens", "case_C_unpriced_model_output", unpriced_out)
note("messages", "case_C_unpriced_model_messages", 15)

# ---------------------------------------------------------------- Case D: window scoping
# One long-lived session: 30 tool_results + 30 messages 10 days ago, then
# 3 messages + 3 tool_results in the last hour. With --days 1, correct tool
# calls in window = 3.
proj4 = "-Users-dev-delta"
sE = hx(8) + "-e"
recsE = []
t = NOW - timedelta(days=10)
for _ in range(30):
    t += timedelta(seconds=60)
    rs, tid, _ = assistant_recs(sE, t, "claude-sonnet-4-6-20260219", [usage()], tool=True)
    recsE += rs
    recsE.append(tool_result(sE, t + timedelta(seconds=2), tid, size=40000))
t = NOW - timedelta(minutes=50)
for _ in range(3):
    t += timedelta(seconds=60)
    rs, tid, _ = assistant_recs(sE, t, "claude-sonnet-4-6-20260219", [usage()], tool=True)
    recsE += rs
    recsE.append(tool_result(sE, t + timedelta(seconds=2), tid, size=400))
write(os.path.join(root, proj4, sE + ".jsonl"), recsE)
note("tool_results", "case_D_in_last_24h", 3)
note("tool_results", "case_D_total_in_file", 33)
note("messages", "case_D_in_last_24h", 3)

with open(os.path.join(home, "manifest.json"), "w") as fh:
    json.dump(manifest, fh, indent=2)
print(json.dumps(manifest, indent=2))
