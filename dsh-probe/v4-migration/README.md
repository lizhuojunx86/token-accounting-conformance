# v4-migration — recompute this catalog's numbers at dsh-v0.2.1-alpha.2 and dsh-v0.2.2-rc.3

Session format v4 is what a current DSH writes: `session-format-catalog/src/generated.ts:17`
has `currentVersion: 4` and `currentEncoder: releasedV4SessionFormatCodec` (line 25),
shipped as tag `dsh-v0.2.1-alpha.2` (`d7432673`). V4 remaps message sources and
tool-result roles, namespaces opaque plugin events, and appends historical child
catalog facts. The usage shape of `assistant/message`, `assistant/attempt` and
`compaction/summary` is unchanged.

The reference corpus was restored v0 → v1 → v2 → v3 → v4 at `dsh-v0.2.1-alpha.2`
and folded with the shipped projection code. Every number in the catalog holds.

| | v0 (Aug 15) | v2 (Sep 5) | v3 at 0.1.5-rc.2 (Sep 12) | v4 at 0.2.1-alpha.2 (Oct 12) |
|---|---|---|---|---|
| events | 8,650 | 303 | 311 | 311 |
| usage samples | 78 | 78 | 78 | 78 |
| naive | 1,122,435 | 1,122,435 | 1,122,435 | 1,122,435 |
| official projection | 536,770 | 536,770 | 536,770 | 536,770 |
| seed-aware | 275,208 | 275,208 | 275,208 | 275,208 |
| correct | 324,103 | 324,103 | 324,103 | 324,103 |
| compaction missed | 48,895 / 3 events | 48,895 / 3 events | 48,895 / 3 events | 48,895 / 3 events |
| fork cuts | seed 1008, 3171 | 47 of 65, 83 of 104 | 49 of 67, 85 of 106 | 49 of 67, 85 of 106 |
| usage written twice inside one settlement | n/a | 36 of 36 agree | 36 of 36 agree | 36 of 36 agree |

Correct per bucket is 66,094 input / 8,281 output / 249,728 cache-read / 0
cache-write, as in August. V4 does not add or drop a single event relative to
v3 on this corpus: the same 311 events, the same two fork cuts, the same 36
settlement dual-writes.

**Re-run 2026-11-14 at `dsh-v0.2.2-rc.3` (`38a6a5c9`, tagged 2026-11-13).** Same
scripts, same corpus, a fresh checkout with `pnpm install --ignore-scripts`,
Node 26.9.0: every number in the table is identical, per session and per
bucket, under both `validation: 'transformed'` and `DSH_VALIDATION=current`,
and all four emitted logs read back under `validation: 'current'`. `results/fold-0.2.2-rc.3.json` is byte-identical to the alpha.2 run.
The source facts below still hold; at rc.3 some sit on shifted lines:
`stateVersion` at `usage-projection.ts:113`, `apply()` at `:116-125`, the
retry slot at `:117-121`, the context-pressure comment at `:157`, and
persistence opens a historical parent at `session-persistence-jsonl/src/index.ts:710`.
`generated.ts:17,25`, `usage-projection.ts:82-86`, `types.ts:53`, `region.ts:504`
and `migration.ts:23-24` are unchanged. D-3 stands at `38a6a5c9`.

## Side findings

**`sessionFormatCatalog.currentVersion` is 4.**
`packages/session/session-format-catalog/src/generated.ts:17` (`currentVersion: 4`,
`currentEncoder: releasedV4SessionFormatCodec` at line 25).

**`tokenUsageProjectionDefinition.stateVersion` is still 2.**
`packages/llm/token-meter/src/usage-projection.ts:119`. Unchanged from the v3
re-measure; the token-usage fold itself did not move.

**`apply()` handles two settlement types and one retry marker; still no
`compaction/summary` branch.**
`usage-projection.ts:122-130` folds `llm/retry-started` (closes the `(turn, step)`
replacement slot) and otherwise only `assistant/message` / `assistant/attempt`,
via `usageOf` at `usage-projection.ts:82-86`. `compaction/summary` appears
nowhere in the file except a comment on `contextPressureProjectionDefinition`
(`usage-projection.ts:163`). D-3 stands at `d7432673`.

**`compaction/summary` still carries `usage` under v4.**
The event type still declares `usage?: TokenUsage`
(`packages/compaction/compaction/src/types.ts:53`), and `compaction-basic` still
writes it at `packages/compaction/compaction-basic/src/region.ts:504`
(`...usage === undefined ? {} : { usage }`). A v4 log therefore still holds the
tokens the official projection cannot see.

**The default catalog refuses a v3→v4 restore without child facts.**
`sessionFormatCatalog.createRestore` on a v0 log throws
`SessionFormatUnsupportedMigrationError: V3 catalog migration requires explicit
historical child facts, including an empty array for a parent without children`
(`packages/session/session-format-v3-to-v4/src/migration.ts:23-24`; the header-only
`sessionFormatV3ToV4.createStage` always throws). The working entry point is
`createSessionFormatCatalogWithChildren(children)` from the same package
(`session-format-catalog/src/children.ts`), which binds
`createSessionFormatV3ToV4(children)`. Persistence takes that route when it
opens a historical parent (`session-persistence-jsonl/src/index.ts:662`,
`createSessionFormatCatalogWithChildren(related.facts)`). This probe passes
`[]` — the documented answer for a parent with no children
(`migration.ts:31`). `readHeader` on the default catalog is unaffected and
still reports `storedVersion: 0, targetVersion: 4`.

**Event totals, fork cuts and settlement dual-write are stable across v2/v3/v4.**
Per-session event counts are 53 / 85 / 67 / 106 at v3 and v4 (303→311 at v2 is
the eight `system/message` events v3 introduced, two per session). The two
seeded sessions cut at inheritedEventCount 49 and 85 (v3 and v4) against 47 and
83 at v2 — the cut moves with the two added `system/message` events, and the
seed prefix still bounds the inherited region. All 36 `assistant/message`
settlements that carry both `data.usage` and a last stream usage chunk hold
identical buckets in every format.

**The retry is unchanged.** Session `e7c289bf`, turn 4 step 1: one
`assistant/attempt` finishing in `TRANSPORT` with usage 0/0, then
`llm/retry` / `llm/retry-started`, then the settled `assistant/message`.
The shipped projection still closes the slot on `llm/retry-started`
(`usage-projection.ts:123-126`); totals unchanged. D-4 still has no measured
magnitude here.

## What changed since the v3 harness

The catalog is the same streaming API (`readHeader` / `createRestore` /
`encodeCurrentHeader` / `encodeCurrentEvent`), but a full-chain restore to v4
must go through `createSessionFormatCatalogWithChildren` rather than the bare
`sessionFormatCatalog` (see Side findings). `emit-v4.ts` keeps `--target 2|3|4`:
3 is the shipped `historicalSessionFormatCatalog` (v0–v3), 2 is a locally
assembled v0–v2 catalog from the same codecs.

The official fold still imports `tokenUsageProjectionDefinition` from
`packages/llm/token-meter/src/usage-projection.ts` and calls its `init()` and
`apply()`. The script lives outside the harness checkout, so it resolves that
file from `cwd` (the checkout root) at load time — same source, not a
transcription.

## Run it

Needs a checkout of `deepseek-ai/deepseek-harness` at `dsh-v0.2.1-alpha.2` or
`dsh-v0.2.2-rc.3` (both measured) and
`pnpm install --ignore-scripts`. Run with that checkout as `cwd` so workspace
names resolve through its `tsconfig` paths:

```
cd <harness checkout>
node --import tsx <this dir>/emit-v4.ts \
  <corpus>/sessions <v4-root> --json fold-0.2.1-alpha.2.json

# re-read what was written under full current validation
node --import tsx <this dir>/roundtrip.ts <v4-root>/*/*/session.jsonl

# stop the chain early if a later stage ever refuses the corpus
node --import tsx <this dir>/emit-v4.ts <corpus>/sessions --target 3 --json fold-v3.json
```

`pnpm exec tsx` also works if the local pnpm can resolve without network;
this sandbox could not, so the recorded runs use `node --import tsx` against
`node_modules/.bin`'s tsx. `DSH_VALIDATION=current` switches the restore to the
stricter mode; the corpus passes both.

## Files

| file | |
|---|---|
| `emit-v4.ts` | restore through the shipped catalog, write target-format logs, fold N/O/S/C with the real projection |
| `roundtrip.ts` | re-read emitted logs with `validation: 'current'` |
| `results/fold-0.2.1-alpha.2.json` | the run behind the table, per session and per bucket |
| `results/fold-0.2.2-rc.3.json` | the same run at `dsh-v0.2.2-rc.3` |
| `package.json` | `{"type":"module"}` so the scripts load as ESM outside the harness tree |

No corpus is committed. Same rule as before: publish measurements, not transcripts.
