# tare-check

Red/green check of [kelviq/tare](https://github.com/kelviq/tare)'s `ccaudit.py`
against a synthetic `~/.claude/projects` with a by-construction manifest. Four
cases, one script, about ten seconds.

Answer at commit `03159f2` (ccaudit 0.2.0, 2026-08-28): three fail, one passes.

```
./check_tare.py                          # clones kelviq/tare at 03159f2, red
./check_tare.py --tare-dir /path/to/tare # an existing checkout
```

Requires git and python3. Stdlib only. Builds a synthetic `$HOME` under a temp
directory; touches no real `~/.claude` data.

## Result

| case | invariant | fixture | manifest | reported at `03159f2` | verdict |
|---|---|---|---|---|---|
| A | I-3 cross-file replay | a 40-message session replayed verbatim into a second file, plus 10 new messages | 50 requests / 102,886 output | 90 / 184,527 | FAIL |
| B | I-2, I-4 sidechain running total | 20 subagent messages × 3 records carrying (1, fin/2, fin) | 40,654 output | 40,654 | PASS |
| C | I-11 unpriced model | 15 requests on a family `MODEL_RATES` has never heard of | loud: weight > 0 or a finding naming it | weight 0.00, share 0.0%, no finding | FAIL |
| D | tool events scoped to `--days` | 30 tool calls ten days ago, 3 in the last hour, `--days 1` | 3 calls / 300 tokens | 33 / 300,300 | FAIL |

Where each one lives in `skills/tare/ccaudit.py` at `03159f2`: `seen` is per
file (`:142`); the `tool_result` branch appends unconditionally (`:174-189`)
while the usage branch is gated by `since` (`:199-201`); `rate_for` returns
`None` for an unmatched family and `weight` turns that into `0.0` (`:297-299`),
and the asterisk/finding path only knows `ESTIMATED_FAMILIES` (`:274-291`,
`:551-565`).

Case A is the fixture at its most extreme (every message replayed). On two
real corpora the cross-file duplication measured 1.095× and 1.0353× (I-3);
[`../clawdmeter-dedup/duplicate_usage_shape.py`](../clawdmeter-dedup/duplicate_usage_shape.py)
measures it read-only on a live tree.

Case B is the one to keep: tare's `if counts["output_tokens"] > prev["output_tokens"]: prev.update(counts)`
is the correct rule for sidechain records, and keep-first would have reported
20 output tokens against 40,654.

Not measured: `amplified` assumes a tool result is re-sent until the session
ends. After a compaction it isn't, so long sessions overstate it by an amount
this fixture does not size.

## Non-vacuity

`fix-1-2-3.patch` is a 36-line change to `ccaudit.py` that gates tool events by
`since`, hoists `seen` into `load_all`, and routes an unmatched family through
the same placeholder-rate path fable/mythos already use. With it applied the
check reads 4 pass, 0 fail, and `--panel`, `--share`, `--html`, `--csv` and
`forensics.py` still run. The patch is here so the harness can be shown to turn
green, not as the shape the maintainer has to pick: case A in particular is a
design choice (what "sessions" means once one spans two files), and the issue
says so.

## Upstream

[kelviq/tare#8](https://github.com/kelviq/tare/issues/8), filed 2026-09-04
with the fixture inlined. Status is tracked in [`../ADOPTERS.md`](../ADOPTERS.md).

## Files

| file | what |
|---|---|
| `check_tare.py` | clone pinned commit → generate corpus → run ccaudit → compare with the manifest, exit 0/1 |
| `gen_fixture.py` | the corpus and `manifest.json`; seed `20260904` |
| `fix-1-2-3.patch` | the change that turns the check green, for non-vacuity |
