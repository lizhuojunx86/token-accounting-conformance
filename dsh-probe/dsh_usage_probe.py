#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Part of traceguard — https://github.com/lizhuojunx86/traceguard
"""
dsh_usage_probe — measure the three token-accounting hazards in DeepSeek Harness
session logs.

Reads DSH session transcripts and computes the same corpus four ways:

  N  naive      every usage sighting summed (chunk + message + compaction)
  O  official   replicates packages/llm/token-meter/src/usage-projection.ts
                (tokenUsageProjectionDefinition) exactly
  S  seed-aware O, but skipping events inherited through a fork seed
                (v0/v1: seq < header.seedLength. v2: seq < the seq of the last
                 session/end-seed {inherited: true} marker)
  C  correct    S, plus compaction/summary.usage

The gaps between them are the findings:

  N - O   dual-write inflation   (v0/v1: usage rides both assistant/chunk and
                                  assistant/message for the same turn/step.
                                  v2: the same two copies live INSIDE one
                                  settlement, as `usage` and as the last usage
                                  chunk of the embedded stream)
  O - S   fork-seed duplication  (a child session's log physically contains a
                                  copy of the parent's completed prefix)
  C - O   compaction undercount  (compaction/summary.usage is not folded by
                                  the official tokenUsage projection)

Two session formats are read. v2 (f99b06ea, "feat(session)!: embed assistant
streams in format v2") removed `assistant/chunk` from the event vocabulary and
replaced the header's numeric `seedLength` with `isSeeded` plus a
`session/end-seed {inherited: true}` marker whose own seq is the cut. Migrating
the reference corpus took it from 8,650 events to 303 and left the number of
places a usage number is written at 75, unchanged.

Nothing here is inferred: each fold is a transcription of shipped source, cited
inline. Run --self-test first; it builds a fixture with known ground truth and
asserts every fold against it.

Usage:
  python3 dsh_usage_probe.py --root /path/to/sessions/root
  python3 dsh_usage_probe.py --root ... --json report.json
  python3 dsh_usage_probe.py --self-test

Stdlib only. `.jsonl.zstd` logs need either the `zstandard` module, a `zstd`
binary, or Node >= 22.15 on PATH (DSH requires Node anyway).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Storage-row tags are bare/slash-less and are NOT session events; they carry no
# usage. packages/core/session/src/chunk-rows.ts whitelists only delta chunks --
# "block-start/end, usage, finish ... stay one event per line" -- so packing can
# never hide a usage sample. Skipping these rows is exact, not an approximation.
CHUNK_ROW_TAGS = {"text-chunks", "reasoning-chunks", "tool-call-chunks"}

BUCKETS = ("uncachedInputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")


# --------------------------------------------------------------------------
# buckets
# --------------------------------------------------------------------------

def zero() -> dict[str, int]:
    return {k: 0 for k in BUCKETS}


def buckets_from(usage: dict) -> dict[str, int]:
    """packages/llm/token-meter/src/usage-projection.ts :: bucketsFrom

    TokenUsage counts are DISJOINT (packages/llm/llm/src/types.ts:135):
    inputTokens is uncached input only, so the four buckets sum without
    double counting. reasoningTokens is a subset of output and is NOT added.
    """
    return {
        "uncachedInputTokens": int(usage.get("inputTokens") or 0),
        "outputTokens": int(usage.get("outputTokens") or 0),
        "cacheReadTokens": int(usage.get("cacheReadTokens") or 0),
        "cacheWriteTokens": int(usage.get("cacheWriteTokens") or 0),
    }


def add(into: dict[str, int], other: dict[str, int]) -> None:
    for k in BUCKETS:
        into[k] += other[k]


def sub(into: dict[str, int], other: dict[str, int]) -> None:
    for k in BUCKETS:
        into[k] -= other[k]


def total(b: dict[str, int]) -> int:
    return sum(b[k] for k in BUCKETS)


def diff(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: a[k] - b[k] for k in BUCKETS}


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------

def _zstd_decode(raw: bytes) -> bytes:
    """Decode a concatenation of independent zstd frames.

    The JSONL backend writes one checksummed frame for the header plus one per
    append batch (session-persistence-jsonl/README.md, "Physical encoding"), so
    a single-frame decode silently returns only the header.
    """
    try:
        import zstandard  # type: ignore

        dctx = zstandard.ZstdDecompressor()
        out = bytearray()
        pos = 0
        while pos < len(raw):
            nxt = raw.find(ZSTD_MAGIC, pos + 4)
            end = len(raw) if nxt == -1 else nxt
            try:
                out += dctx.decompress(raw[pos:end])
            except Exception:
                # Incomplete tail frame: crash-recovery territory. Stop here and
                # report; do not guess at partial content.
                break
            pos = end
        return bytes(out)
    except ImportError:
        pass

    if shutil.which("zstd"):
        with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        try:
            # -d decodes concatenated frames; -c to stdout.
            return subprocess.run(["zstd", "-d", "-c", tmp], capture_output=True, check=True).stdout
        finally:
            os.unlink(tmp)

    helper = Path(__file__).with_name("zstd_cat.mjs")
    if shutil.which("node") and helper.exists():
        with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        try:
            return subprocess.run(["node", str(helper), tmp], capture_output=True, check=True).stdout
        finally:
            os.unlink(tmp)

    raise RuntimeError(
        "cannot decode .jsonl.zstd -- install `zstandard` (pip), the `zstd` binary, "
        "or keep Node on PATH. Easiest: write the probe corpus with "
        "compression: 'none' (see PROTOCOL.md)."
    )


def read_records(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if path.name.endswith(".zstd"):
        raw = _zstd_decode(raw)
    out = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------
# folds
# --------------------------------------------------------------------------

def _last_stream_usage(stream) -> dict | None:
    """Last usage chunk embedded in a v2 assistant stream.

    usage-projection.ts (v2) walks `expandAssistantStream(...).toReversed()` and
    returns the first usage chunk it meets, i.e. the last one in order.
    """
    if not isinstance(stream, list):
        return None
    for member in reversed(stream):
        chunk = member.get("chunk") if isinstance(member, dict) else None
        if chunk is None and isinstance(member, dict):
            chunk = member
        if isinstance(chunk, dict) and chunk.get("type") == "usage":
            return chunk.get("usage")
    return None


def usage_of(ev: dict, fmt: int = 0) -> dict | None:
    """usage-projection.ts :: usageOf -- what the OFFICIAL projection can see.

    Two shapes, because the event model changed under it:

    v0/v1  a usage stream chunk is its own top-level `assistant/chunk` event,
           and the assembled `assistant/message` carries `usage` as well.
    v2     one durable settlement per attempt (`assistant/message`, or
           `assistant/attempt` when no surface message exists) with the stream
           embedded. Landed in f99b06ea, "feat(session)!: embed assistant
           streams in format v2"; `assistant/chunk` is gone from the vocabulary
           (packages/core/session/src/known-event-types.ts).
    """
    t = ev.get("type")
    if fmt < 2:
        if t == "assistant/chunk":
            chunk = (ev.get("data") or {}).get("chunk") or {}
            if chunk.get("type") == "usage":
                return chunk.get("usage")
            return None
        if t == "assistant/message":
            return (ev.get("data") or {}).get("usage")
        return None
    if t == "assistant/message":
        d = ev.get("data") or {}
        if d.get("usage") is not None:
            return d.get("usage")
        return _last_stream_usage(d.get("stream"))
    if t == "assistant/attempt":
        return _last_stream_usage((ev.get("data") or {}).get("stream"))
    return None


def sightings_of(ev: dict, fmt: int = 0) -> list[dict]:
    """Every distinct place this event writes a usage number.

    This is the D-1 exhibit and it is NOT `usage_of`. Under v2 a single
    `assistant/message` carries `usage` AND a stream whose last usage chunk
    holds the same numbers, so the duplication that used to span two events now
    sits inside one. A consumer that expands the stream (because that is where
    the chunks used to live) and then also reads `.usage` doubles exactly as
    before, with nothing in the log to suggest it.
    """
    out: list[dict] = []
    if fmt < 2:
        u = usage_of(ev, fmt)
        if u is not None:
            out.append(u)
        return out
    t = ev.get("type")
    d = ev.get("data") or {}
    if t == "assistant/message":
        if d.get("usage") is not None:
            out.append(d["usage"])
        s = _last_stream_usage(d.get("stream"))
        if s is not None:
            out.append(s)
    elif t == "assistant/attempt":
        s = _last_stream_usage(d.get("stream"))
        if s is not None:
            out.append(s)
    return out


def count_intra_event_dual_write(events: list[dict], fmt: int) -> tuple[int, int]:
    """(settlements carrying usage in two places, of which the two agree).

    Only meaningful for v2. Under v0/v1 the two copies are separate events, so
    this returns (0, 0) and the dual write shows up in N' / O instead.
    """
    if fmt < 2:
        return (0, 0)
    both = agree = 0
    for ev in events:
        if ev.get("type") != "assistant/message":
            continue
        d = ev.get("data") or {}
        u, s = d.get("usage"), _last_stream_usage(d.get("stream"))
        if u is None or s is None:
            continue
        both += 1
        if buckets_from(u) == buckets_from(s):
            agree += 1
    return (both, agree)


def inherited_cut(header: dict, events: list[dict], fmt: int) -> int:
    """First seq of the session's own work; everything below it is the parent's.

    v0/v1  header.seedLength, a numeric cut.
    v2     header.isSeeded plus the seq of the LAST
           `session/end-seed {inherited: true}` marker. The marker's own seq IS
           the cut, so the marker is the first event of the child's own work --
           do not add one. Upstream derives it exactly this way in
           session-format-v1-to-v2/src/codec.ts deriveInheritedEventCount, and
           validation.ts asserts `lastInheritedMarker === cut`.

    A v2 header carrying `seedLength` is rejected outright by the current
    Session package (packages/core/session/src/index.ts:98-100, "session header
    has invalid field \\"seedLength\\""), so the old discriminator is not merely
    absent, it is forbidden.
    """
    if fmt < 2:
        return int(header.get("seedLength") or 0)
    if not header.get("isSeeded"):
        return 0
    cut = 0
    for ev in events:
        if ev.get("type") == "session/end-seed" and (ev.get("data") or {}).get("inherited") is True:
            cut = int(ev.get("seq", 0))
    return cut


def fold_official(events: list[dict], fmt: int = 0, retry_aware: bool = False) -> dict[str, int]:
    """Transcription of tokenUsageProjectionDefinition.apply.

    Single `last` slot; a repeated (turn, step) sample REPLACES the earlier
    value (addReplacing) instead of double counting. An identical repeat is a
    no-op via bucketsEqual.

    retry_aware=False is stateVersion 1 (v0.1.0-rc.5, what the published
    measurements were taken against). retry_aware=True is stateVersion 2, where
    `llm/retry-started` clears the replacement slot so a retried attempt adds
    instead of overwriting the attempt that died. Both are kept because the
    difference is the whole of D-4 and it is zero on a corpus whose failed
    attempts all reported zeros.
    """
    totals = zero()
    last: tuple[int, int, dict[str, int]] | None = None
    for ev in events:
        if ev.get("type") == "llm/retry-started":
            if retry_aware:
                last = None
            continue
        u = usage_of(ev, fmt)
        if u is None:
            continue
        d = ev.get("data") or {}
        turn, step = d.get("turn"), d.get("step")
        b = buckets_from(u)
        previous = last[2] if (last and last[0] == turn and last[1] == step) else None
        if previous is not None and previous == b:
            continue
        if previous is not None:
            sub(totals, previous)
        add(totals, b)
        last = (turn, step, b)
    return totals


def fold_naive(events: list[dict], fmt: int = 0) -> dict[str, int]:
    """The obvious-and-wrong implementation: see a usage, add it.

    Includes compaction/summary.usage, because a naive walker has no reason to
    skip it -- which is why naive is not uniformly higher than correct.

    Under v2 this sums both halves of the intra-event dual write, which is the
    point: the migrated corpus goes from 8,650 events to 303 and the number of
    places a usage number is written stays at 75.
    """
    totals = zero()
    for ev in events:
        seen = sightings_of(ev, fmt)
        if not seen and ev.get("type") == "compaction/summary":
            u = (ev.get("data") or {}).get("usage")
            if u is not None:
                seen = [u]
        for u in seen:
            add(totals, buckets_from(u))
    return totals


def fold_compaction(events: list[dict]) -> dict[str, int]:
    """compaction/summary.usage -- provider-reported cost of the summarize call.

    Written at packages/compaction/compaction-basic/src/region.ts:447; typed at
    packages/compaction/compaction/src/types.ts ("Provider-reported token usage
    for the summarization request, when emitted"). usageOf() does not match it,
    so the official tokenUsage projection never folds it.
    """
    totals = zero()
    for ev in events:
        if ev.get("type") != "compaction/summary":
            continue
        u = (ev.get("data") or {}).get("usage")
        if u:
            add(totals, buckets_from(u))
    return totals


# --------------------------------------------------------------------------
# session model
# --------------------------------------------------------------------------

@dataclass
class SessionReport:
    path: str
    session_id: str
    parent: str | None
    seed_length: int
    delegation_depth: int
    origin: str | None
    n_events: int
    n_usage_sightings: int
    n_compaction_summaries: int
    naive: dict[str, int]
    official: dict[str, int]
    seed_aware: dict[str, int]
    correct: dict[str, int]
    seq_contiguous: bool
    format_version: int = 0
    n_dual_write_sites: int = 0
    n_dual_write_agree: int = 0
    notes: list[str] = field(default_factory=list)


def analyze_log(path: Path) -> SessionReport:
    records = read_records(path)
    if not records:
        raise ValueError(f"{path}: empty log")

    header = records[0]
    if header.get("type") != "session":
        raise ValueError(f"{path}: first line is not a session header")

    events = [r for r in records[1:] if r.get("type") not in CHUNK_ROW_TAGS]

    fmt = int(header.get("version") or 0)
    notes: list[str] = []
    if fmt > 2:
        notes.append(
            f"session format v{fmt} is newer than this probe understands (v2). "
            "The folds below transcribe the v2 event model, so treat them as "
            "unverified until the reader is updated."
        )
    seed_length = inherited_cut(header, events, fmt)

    seqs = [e.get("seq") for e in records[1:] if e.get("type") not in CHUNK_ROW_TAGS and "seq" in e]
    contiguous = all(b - a == 1 for a, b in zip(seqs, seqs[1:])) if len(seqs) > 1 else True
    if not contiguous:
        notes.append(
            "seq gaps present -- expected when packChunks is on (packed rows were "
            "skipped). Re-run the probe corpus with packChunks: false to assert "
            "contiguity directly."
        )

    own = [e for e in events if int(e.get("seq", 0)) >= seed_length]

    naive = fold_naive(events, fmt)
    official = fold_official(events, fmt)
    seed_aware = fold_official(own, fmt)
    correct = dict(seed_aware)
    add(correct, fold_compaction(own))

    n_sight = sum(
        len(sightings_of(e, fmt))
        or (1 if e.get("type") == "compaction/summary" and (e.get("data") or {}).get("usage") else 0)
        for e in events
    )
    n_compact = sum(1 for e in own if e.get("type") == "compaction/summary")
    dual_both, dual_agree = count_intra_event_dual_write(events, fmt)

    if fmt < 2 and seed_length and not any(e.get("type") == "session/end-seed" for e in events):
        notes.append(
            "header declares seedLength but no session/end-seed marker was found "
            "in the log -- a reader relying on the marker alone would mis-slice."
        )
    if fmt >= 2 and header.get("seedLength") is not None:
        notes.append(
            "v2 header carries seedLength, which the current Session package "
            "rejects outright (packages/core/session/src/index.ts:98-100). This "
            "log was not written by a conforming v2 writer."
        )
    if dual_both:
        notes.append(
            f"intra-event dual write: {dual_both} assistant/message event(s) carry "
            f"usage in two places (the `usage` field and the embedded stream's last "
            f"usage chunk); {dual_agree} of them hold identical numbers. Read one, "
            "not both."
        )

    return SessionReport(
        path=str(path),
        session_id=str(header.get("id")),
        parent=header.get("parentSession"),
        seed_length=seed_length,
        delegation_depth=int(header.get("delegationDepth") or 0),
        origin=header.get("origin"),
        n_events=len(events),
        n_usage_sightings=n_sight,
        n_compaction_summaries=n_compact,
        naive=naive,
        official=official,
        seed_aware=seed_aware,
        correct=correct,
        seq_contiguous=contiguous,
        format_version=fmt,
        n_dual_write_sites=dual_both,
        n_dual_write_agree=dual_agree,
        notes=notes,
    )


def discover(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    out: list[Path] = []
    for p in sorted(root.rglob("session.jsonl*")):
        if p.name in ("session.jsonl", "session.jsonl.zstd"):
            out.append(p)
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def pct(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{num / den * 100:+.1f}%"


def report(reports: list[SessionReport]) -> dict:
    agg = {k: zero() for k in ("naive", "official", "seed_aware", "correct")}
    for r in reports:
        add(agg["naive"], r.naive)
        add(agg["official"], r.official)
        add(agg["seed_aware"], r.seed_aware)
        add(agg["correct"], r.correct)

    c = total(agg["correct"])
    print()
    print("=" * 78)
    print(f"DSH token accounting probe -- {len(reports)} session log(s)")
    print("=" * 78)
    print()
    print(f"{'fold':<26}{'total':>14}{'vs correct':>14}  {'':<10}")
    print("-" * 78)
    for key, label in (
        ("naive", "N  naive"),
        ("official", "O  official projection"),
        ("seed_aware", "S  seed-aware"),
        ("correct", "C  correct"),
    ):
        t = total(agg[key])
        print(f"{label:<26}{t:>14,}{pct(t - c, c):>14}")
    print()

    print("mechanism attribution")
    print("-" * 78)
    d_comp = total(agg["correct"]) - total(agg["seed_aware"])
    # Naive also folds compaction/summary, which the official projection skips.
    # Subtracting it first keeps this line measuring dual-write ALONE; leaving it
    # in would silently credit the compaction gap to the wrong mechanism.
    naive_excl_compaction = total(agg["naive"]) - d_comp
    d_dual = naive_excl_compaction - total(agg["official"])
    d_seed = total(agg["official"]) - total(agg["seed_aware"])
    print(f"  dual-write inflation    N'-O  {d_dual:>+14,}   {pct(d_dual, c)}")
    print(f"  fork-seed duplication    O-S  {d_seed:>+14,}   {pct(d_seed, c)}")
    print(f"  compaction undercount    C-S  {d_comp:>+14,}   {pct(d_comp, c)}")
    if total(agg["official"]) > 0:
        ratio = naive_excl_compaction / total(agg["official"])
        print()
        print(f"  N' / O = {ratio:.6f}   (N' = naive minus compaction; 2.000000 means every")
        print(f"           usage sample was written exactly twice, with no exceptions)")
    print()

    print("per bucket (correct)")
    print("-" * 78)
    for k in BUCKETS:
        print(f"  {k:<24}{agg['correct'][k]:>14,}")
    print()

    print("per session")
    print("-" * 78)
    for r in reports:
        # Three distinct shapes, and the middle one is the finding: an ORDINARY
        # session fork (ctx.sessions.fork) carries parentSession + seedLength but
        # NO origin and NO delegation-depth bump. The codebase's own subagent
        # lineage index skips anything whose origin !== 'subagent'
        # (client/sessions/subagent-lineage.ts), so an origin-based filter never
        # sees these. `seedLength` is the only sound discriminator.
        if r.origin == "subagent":
            kind = "subagent"
        elif r.seed_length > 0:
            kind = "fork" if not r.origin else r.origin
        else:
            kind = r.origin or "root"
        print(
            f"  {r.session_id[:24]:<26}{kind:<10}v{r.format_version} "
            f"depth={r.delegation_depth} "
            f"seed={r.seed_length:<6}events={r.n_events:<7}"
            f"usage={r.n_usage_sightings:<5}compact={r.n_compaction_summaries}"
        )
        if r.n_dual_write_sites:
            print(
                f"      intra-event dual write: {r.n_dual_write_sites} settlement(s) "
                f"carry usage twice, {r.n_dual_write_agree} identical"
            )
        if total(r.official) != total(r.seed_aware):
            own = total(r.seed_aware)
            over = f"{total(r.official) / own:.2f}x" if own else "inf"
            print(
                f"      seed duplication: official {total(r.official):,} "
                f"vs own-only {own:,} "
                f"({total(r.official) - own:+,}, {over} overstated)"
            )
            if r.origin != "subagent":
                print(
                    "      ^ NOT a subagent: origin absent, delegationDepth="
                    f"{r.delegation_depth}. An origin-based filter misses this."
                )
        if total(r.correct) != total(r.seed_aware):
            print(
                f"      compaction missed: {total(r.correct) - total(r.seed_aware):+,} "
                f"across {r.n_compaction_summaries} summary event(s)"
            )
        for n in r.notes:
            print(f"      note: {n}")
    print()

    return {
        "sessions": [r.__dict__ for r in reports],
        "aggregate": agg,
        "deltas": {
            "dual_write": d_dual,
            "fork_seed": d_seed,
            "compaction": d_comp,
            "correct_total": c,
        },
    }


# --------------------------------------------------------------------------
# self-test: validate every fold against a fixture with known ground truth
# --------------------------------------------------------------------------

def build_fixture(dest: Path) -> dict:
    """Emit a parent + forked-child pair exercising all three hazards.

    Ground truth is constructed, not measured, so the folds can be asserted
    before the probe is pointed at a real corpus. This mirrors the discipline in
    the rest of this repository: validate the checker on synthetic data first.
    """
    def usage(i, o, cr=0, cw=0):
        return {"inputTokens": i, "outputTokens": o, "cacheReadTokens": cr, "cacheWriteTokens": cw}

    def ev(seq, type_, data):
        return {"type": type_, "seq": seq, "time": 1_770_000_000_000 + seq, "data": data}

    # ---- parent -----------------------------------------------------------
    # turn 0 step 0: usage reported twice (stream chunk, then assistant message)
    # turn 0 step 1: usage reported once (message only)
    # one compaction whose summary call cost 300+150
    parent = [
        {"type": "session", "version": 0, "id": "parent-1", "cwd": "/probe",
         "createdAt": 1_770_000_000_000, "delegationDepth": 0},
        ev(0, "user/message", {"turn": 0}),
        ev(1, "assistant/chunk", {"turn": 0, "step": 0,
                                  "chunk": {"type": "usage", "usage": usage(1000, 200, 50, 10)}}),
        ev(2, "assistant/message", {"turn": 0, "step": 0, "message": {},
                                    "usage": usage(1000, 200, 50, 10)}),
        ev(3, "assistant/chunk", {"turn": 0, "step": 1,
                                  "chunk": {"type": "usage", "usage": usage(2000, 400, 0, 0)}}),
        ev(4, "assistant/message", {"turn": 0, "step": 1, "message": {},
                                    "usage": usage(2000, 400, 0, 0)}),
        ev(5, "turn/end", {"turn": 0}),
        ev(6, "compaction/start", {"compactionId": "c1", "turn": None}),
        ev(7, "compaction/summary", {"compactionId": "c1", "summary": [],
                                     "shadowedRange": {"start": 0, "end": 5},
                                     "shadowedSeqs": [0, 1, 2, 3, 4, 5],
                                     "shadowedTokenCount": 3660,
                                     "provider": "deepseek", "model": "deepseek-v4",
                                     "usage": usage(300, 150, 0, 0)}),
        ev(8, "user/message", {"turn": 0, "surfaceOp": {"op": "replace", "start": 0, "end": 5}}),
        ev(9, "compaction/end", {"compactionId": "c1", "turn": None}),
    ]

    # ---- child: fork seed = parent's completed prefix (seq 0..5) -----------
    seed = [dict(e) for e in parent[1:7]]  # seq 0..5, incl. the duplicated usage
    child = [
        {"type": "session", "version": 0, "id": "child-1", "cwd": "/probe",
         "createdAt": 1_770_000_000_100, "parentSession": "parent-1",
         "seedLength": 7, "origin": "subagent", "delegationDepth": 1},
        *seed,
        ev(6, "session/end-seed", {}),
        ev(7, "assistant/chunk", {"turn": 1, "step": 0,
                                  "chunk": {"type": "usage", "usage": usage(500, 100, 0, 0)}}),
        ev(8, "assistant/message", {"turn": 1, "step": 0, "message": {},
                                    "usage": usage(500, 100, 0, 0)}),
        ev(9, "turn/end", {"turn": 1}),
    ]

    for name, log in (("parent-1", parent), ("child-1", child)):
        d = dest / "--probe--" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in log) + "\n", encoding="utf-8"
        )

    # ground truth, by hand
    parent_real = (1000 + 200 + 50 + 10) + (2000 + 400)          # 3660
    parent_compaction = 300 + 150                                 # 450
    child_own = 500 + 100                                         # 600
    return {
        "correct": parent_real + parent_compaction + child_own,   # 4710
        "seed_aware": parent_real + child_own,                    # 4260
        "official": parent_real + child_own + parent_real,        # 7920 (seed counted twice)
        # naive: every sighting, incl. both halves of each dual write
        "naive": (parent_real * 2 + parent_compaction)            # 7770
                 + (parent_real * 2 + child_own * 2),             # 8520  -> 16290
    }


def build_fixture_v2(dest: Path) -> dict:
    """The same two sessions in the released-v2 event model.

    Same ground truth as build_fixture, expressed the way a current DSH writes
    it: one durable settlement per attempt with the stream embedded, no
    top-level `assistant/chunk`, and the inherited cut carried by a
    `session/end-seed {inherited: true}` marker instead of `seedLength`.

    The dual write survives the change. Each `assistant/message` below holds the
    same numbers in `usage` and in the last usage chunk of its stream, which is
    what the v2 half of D-1 asserts.
    """
    def usage(i, o, cr=0, cw=0):
        return {"inputTokens": i, "outputTokens": o, "cacheReadTokens": cr, "cacheWriteTokens": cw}

    def ev(seq, type_, data):
        return {"type": type_, "seq": seq, "time": 1_770_000_000_000 + seq, "data": data}

    def settled(seq, turn, step, u):
        # usage in BOTH places, exactly as the migration and the live writer emit it
        return ev(seq, "assistant/message", {
            "turn": turn, "step": step, "message": {}, "usage": u,
            "stream": [{"chunk": {"type": "usage", "usage": u}}],
        })

    parent = [
        {"type": "session", "version": 2, "id": "parent-2", "cwd": "/probe",
         "createdAt": 1_770_000_000_000, "isSeeded": False, "delegationDepth": 0},
        ev(0, "user/message", {"turn": 0}),
        settled(1, 0, 0, usage(1000, 200, 50, 10)),
        settled(2, 0, 1, usage(2000, 400, 0, 0)),
        ev(3, "turn/end", {"turn": 0}),
        ev(4, "compaction/start", {"compactionId": "c1", "turn": None}),
        ev(5, "compaction/summary", {"compactionId": "c1", "summary": [],
                                     "shadowedRange": {"start": 0, "end": 3},
                                     "shadowedSeqs": [0, 1, 2, 3],
                                     "shadowedTokenCount": 3660,
                                     "provider": "deepseek", "model": "deepseek-v4",
                                     "usage": usage(300, 150, 0, 0)}),
        ev(6, "user/message", {"turn": 0, "surfaceOp": {"op": "replace", "start": 0, "end": 3}}),
        ev(7, "compaction/end", {"compactionId": "c1", "turn": None}),
    ]

    # Child inherits seq 0..3. The marker sits AT the cut, so its own seq is the
    # first event of the child's own work -- getting this off by one is the
    # mistake the v2 rule invites.
    seed = [dict(e) for e in parent[1:5]]
    child = [
        {"type": "session", "version": 2, "id": "child-2", "cwd": "/probe",
         "createdAt": 1_770_000_000_100, "parentSession": "parent-2",
         "isSeeded": True, "delegationDepth": 0, "agentPreset": "standard"},
        *seed,
        ev(4, "session/end-seed", {"inherited": True}),
        settled(5, 1, 0, usage(500, 100, 0, 0)),
        ev(6, "turn/end", {"turn": 1}),
    ]

    for name, log in (("parent-2", parent), ("child-2", child)):
        d = dest / "--probe-v2--" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in log) + "\n", encoding="utf-8"
        )

    parent_real = (1000 + 200 + 50 + 10) + (2000 + 400)          # 3660
    parent_compaction = 300 + 150                                 # 450
    child_own = 500 + 100                                         # 600
    return {
        "correct": parent_real + parent_compaction + child_own,   # 4710
        "seed_aware": parent_real + child_own,                    # 4260
        "official": parent_real + child_own + parent_real,        # 7920
        # naive: both halves of every intra-event dual write, plus compaction
        "naive": (parent_real * 2 + parent_compaction)
                 + (parent_real * 2 + child_own * 2),             # 16290
        # 3 settlements in the parent's own log + 3 in the child (2 seeded + 1 own)
        "dual_write_sites": 5,
        "cut": 4,
    }


def _run_fixture(label: str, builder, dest: Path) -> tuple[bool, list[SessionReport], dict]:
    truth = builder(dest)
    reports = [analyze_log(p) for p in discover(dest)]
    agg = {k: zero() for k in ("naive", "official", "seed_aware", "correct")}
    for r in reports:
        add(agg["naive"], r.naive)
        add(agg["official"], r.official)
        add(agg["seed_aware"], r.seed_aware)
        add(agg["correct"], r.correct)

    ok = True
    print()
    print(f"self-test [{label}] -- folds vs hand-computed ground truth")
    print("-" * 62)
    for k in ("naive", "official", "seed_aware", "correct"):
        got, want = total(agg[k]), truth[k]
        mark = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(f"  {k:<14}{got:>10,}   expected {want:>10,}   {mark}")

    # v2-only structural assertions: the dual write must still be visible, and
    # the inherited cut must land on the marker's own seq rather than past it.
    if "dual_write_sites" in truth:
        got = sum(r.n_dual_write_sites for r in reports)
        want = truth["dual_write_sites"]
        mark = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(f"  {'dual-write sites':<14}{got:>10,}   expected {want:>10,}   {mark}")
        agree = sum(r.n_dual_write_agree for r in reports)
        mark = "PASS" if agree == want else "FAIL"
        ok &= agree == want
        print(f"  {'  of which agree':<14}{agree:>10,}   expected {want:>10,}   {mark}")
    if "cut" in truth:
        seeded = [r for r in reports if r.seed_length]
        got = seeded[0].seed_length if seeded else -1
        want = truth["cut"]
        mark = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(f"  {'inherited cut':<14}{got:>10,}   expected {want:>10,}   {mark}")

    print()
    print(f"  fixture at {dest}")
    return ok, reports, truth


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dsh-probe-fixture-"))
    ok_v1, _, _ = _run_fixture("v0/v1", build_fixture, tmp / "v1")
    ok_v2, _, _ = _run_fixture("v2", build_fixture_v2, tmp / "v2")
    ok = ok_v1 and ok_v2
    print()
    if ok:
        print("  All folds reproduce the constructed truth in both event models.")
        print("  The probe may now be pointed at a real corpus.")
    else:
        print("  A fold disagrees with the fixture. Fix the probe before running it")
        print("  on real data -- do not publish anything measured with it.")
    print()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, help="DSH sessions root (or one session.jsonl)")
    ap.add_argument("--json", type=Path, help="write the full report as JSON")
    ap.add_argument("--self-test", action="store_true", help="validate folds against a fixture and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.root:
        ap.error("--root is required (or use --self-test)")

    logs = discover(args.root)
    if not logs:
        print(f"no session logs found under {args.root}", file=sys.stderr)
        print("expected <root>/--<cwd>--/<id>/session.jsonl[.zstd]", file=sys.stderr)
        return 2

    reports = []
    for p in logs:
        try:
            reports.append(analyze_log(p))
        except Exception as exc:  # noqa: BLE001
            print(f"  skipped {p}: {exc}", file=sys.stderr)

    if not reports:
        return 2

    payload = report(reports)
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
