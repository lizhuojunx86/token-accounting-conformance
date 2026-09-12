# v3-migration — recompute this catalog's numbers at dsh-v0.1.5-rc.2

Session format v3 is what a current DSH writes: `session-format-catalog/src/generated.ts`
has `currentEncoder: releasedV3SessionFormatCodec` since `f7a6221158` (2026-09-06), and
every tag from `dsh-v0.1.5-alpha.1` (2026-09-08) ships it. V3 promotes the system prompt
into `system/message` events, remaps references and envelopes, and leaves the usage
shape of `assistant/message`, `assistant/attempt` and `compaction/summary` alone.

The reference corpus was restored v0 → v1 → v2 → v3 at `dsh-v0.1.5-rc.2` (`a3053034`,
2026-09-10) and folded with the shipped projection code. Every number in the catalog
holds.

| | v0 (Aug 15) | v2 (Sep 5) | v3 at 0.1.5-rc.2 (Sep 12) |
|---|---|---|---|
| events | 8,650 | 303 | 311 |
| usage samples | 78 | 78 | 78 |
| naive | 1,122,435 | 1,122,435 | 1,122,435 |
| official projection | 536,770 | 536,770 | 536,770 |
| seed-aware | 275,208 | 275,208 | 275,208 |
| correct | 324,103 | 324,103 | 324,103 |
| compaction missed | 48,895 / 3 events | 48,895 / 3 events | 48,895 / 3 events |
| fork cuts | seed 1008, 3171 | 47 of 65, 83 of 104 | 49 of 67, 85 of 106 |
| usage written twice inside one settlement | n/a | 36 of 36 agree | 36 of 36 agree |

The 8 extra v3 events are two `system/message` per session. Correct per bucket is
66,094 input / 8,281 output / 249,728 cache-read / 0 cache-write, as in August.

## What changed since the v2 harness

The migration API is streaming now. `decodeArtifact` / `migrate` / `validateTarget` /
`encodeArtifact` from `emit-v2.ts` are gone; a codec exposes `decodeHeader`,
`createDecoder`, `encodeHeader`, `encodeEvent`, a migration exposes `migrateHeader`,
`createStage`, `validateTargetHeader`, and the catalog exposes `readHeader`,
`createRestore`, `encodeCurrentHeader`, `encodeCurrentEvent`. `emit-v3.ts` goes through
`sessionFormatCatalog.createRestore` with `validation: 'transformed'`, which is the call
`session-persistence-jsonl` makes when it opens a stored log, so the events it folds are
the events a running host would see.

The official fold is no longer a transcription. `emit-v3.ts` imports
`tokenUsageProjectionDefinition` from `packages/llm/token-meter/src/usage-projection.ts`
and calls its `init()` and `apply()`. If upstream changes the projection, this fold
changes with it.

## Run it

Needs a checkout of `deepseek-ai/deepseek-harness` and `pnpm install --ignore-scripts`.
The scripts import workspace packages by name and the projection by relative path, so
they run from `scripts/probe/` inside that checkout.

```
git clone --depth 1 --branch dsh-v0.1.5-rc.2 \
  https://github.com/deepseek-ai/deepseek-harness.git repo
cd repo && corepack pnpm install --ignore-scripts
mkdir -p scripts/probe && cp <this dir>/emit-v3.ts <this dir>/roundtrip.ts scripts/probe/

# restore, write current-format logs, fold four ways
pnpm exec tsx scripts/probe/emit-v3.ts <corpus>/sessions <v3-root> --json fold.json

# re-read what was written under full current validation
pnpm exec tsx scripts/probe/roundtrip.ts <v3-root>/*/*/session.jsonl

# the Python probe reads v3 with a "newer than this probe understands" note; same numbers
python3 ../dsh_usage_probe.py --root <v3-root>
```

`--target 2` stops the chain at released v2 for a corpus the v2 → v3 stage refuses. Two
of upstream's own fixtures are refused (`released-v0-real-shapes.jsonl`,
`webworker-runtime/.../preview-showcase/session.v2.jsonl`: "surface before first step
cannot acquire a system head without changing chronology"); the reference corpus is not,
because in every session the first `user/message` comes after the first `step/start`.
`DSH_VALIDATION=current` switches the restore to the stricter mode; the corpus passes
both.

## The rc.6 corpus no longer needs `strip-replaystate.mjs`

The v2 harness had to drop `replayState` from finish chunks before the frozen v0 reader
would accept a log written by `0.1.0-rc.6`. Since `46196d6f95` (2026-09-06, "perf(session-format):
stream released v0-to-v2 migrations", in `dsh-v0.1.3-alpha.2` and later)
`session-format-v0-to-v1/src/migration.ts:97` skips `assertReleasedEventPayload` for
`assistant/chunk`, which is the only place `replayState` lives. The raw corpus restores
under both validation modes, and the folds match the stripped run bucket for bucket.
The statement in the Sep 5 update that a current DSH refuses logs of that vintage is
withdrawn.

## The retry the corpus does contain

Session `e7c289bf`, turn 4 step 1: an `assistant/attempt` whose stream ends in
`finish {kind: 'error', failure: {code: 'TRANSPORT'}}` with usage 0/0, then `llm/retry`
and `llm/retry-started`, then the settled `assistant/message`. The shipped projection
(stateVersion 2) closes the slot on `llm/retry-started` and the totals are unchanged.
That attempt reported zeros, so D-4 still has no measured magnitude here.

## dsh-token-cost on v3

`le-soleil-se-couche/dsh-token-cost` at `35c06cf` accepts formats v0–v2 and throws
`unsupported session format version v3` on all four emitted logs. `dsh-token-cost-v3-gate.patch`
widens the gate to 3 in `src/parser.ts` (six lines, nothing else); with it the parser
returns 324,103, per session 11,439 / 252,348 / 4,430 / 55,886, one compaction record
where a compaction happened. `token-cost-v3-probe.mts` is the check:

```
TOKEN_COST_REPO=<dsh-token-cost checkout> V3_CORPUS=<v3-root> \
  node --experimental-strip-types token-cost-v3-probe.mts
```

On this corpus the v2 accounting grammar carries over to v3 unchanged. That is one
corpus and one plugin; a V3 log with a failed summarizer or a `cacheWriteTokens` provider
is still unmeasured.

## Files

| file | |
|---|---|
| `emit-v3.ts` | restore through the shipped catalog, write current-format logs, fold N/O/S/C with the real projection |
| `roundtrip.ts` | re-read emitted logs with `validation: 'current'` |
| `results/fold-0.1.5-rc.2.json` | the run behind the table, per session and per bucket |
| `dsh-token-cost-v3-gate.patch` | the six-line version-gate change for dsh-token-cost at `35c06cf` |
| `token-cost-v3-probe.mts` | runs dsh-token-cost's parser over a v3 corpus |

No corpus is committed. Same rule as before: publish measurements, not transcripts.
