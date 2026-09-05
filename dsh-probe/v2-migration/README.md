# v2-migration — recompute this catalog's numbers under session format v2

Session format v2 landed 2026-09-01 in `f99b06ea`, "feat(session)!: embed
assistant streams in format v2". It removed `assistant/chunk` from the event
vocabulary and replaced the header's numeric `seedLength` with `isSeeded` plus a
`session/end-seed {inherited: true}` marker. Three entries in
[`CONFORMANCE-DSH.md`](../../CONFORMANCE-DSH.md) are worded in terms of the old
shapes, so the question is whether they still measure anything.

They do. Migrating the reference corpus takes it from **8,650 events to 303**
and leaves the number of places a usage number is written at **75**. Every fold
returns the same total in both formats.

| | v0 | v2 |
|---|---|---|
| events | 8,650 | 303 |
| usage sightings | 75 | 75 |
| naive | 1,122,435 | 1,122,435 |
| official projection | 536,770 | 536,770 |
| seed-aware | 275,208 | 275,208 |
| correct | 324,103 | 324,103 |
| N′ / O | 2.000000 | 2.000000 |
| intra-event dual write | n/a | 36 of 36 identical |

## No corpus is committed here

The measured corpus is private session transcripts. What is committed is the
harness that reproduces the comparison on *your* logs, plus the synthetic v2
fixture that `dsh_usage_probe.py --self-test` builds and asserts in under a
second with no corpus at all. Run the self-test first; it covers both event
models.

## Reproducing the migration on your own corpus

Needs a checkout of `deepseek-ai/deepseek-harness` (the format packages are not
published to npm) and `pnpm install`.

```
git clone --depth 1 --branch dsh-v0.1.3-alpha.1 \
  https://github.com/deepseek-ai/deepseek-harness.git repo
cd repo && pnpm install --ignore-scripts

# 1. strip an optional field the frozen v0 reader refuses (see below)
node strip-replaystate.mjs <corpus> <corpus-stripped>

# 2. v0 -> v1 -> v2, both migrations validating their target
pnpm exec tsx emit-v2.ts <corpus-stripped> <v2-root>

# 3. fold both and compare
node fold.mjs v0 <corpus-stripped>
node fold.mjs v2 <v2-root>
python3 ../dsh_usage_probe.py --root <v2-root>/sessions
```

`migrate.ts` is the same conversion without the physical encode step, useful
when you only want the logical v2 events as JSON.

## The v0 reader refuses logs written by 0.1.0-rc.6

```
SessionFormatError: assistant/chunk 179 chunk replayState has unexpected member "kind"
```

`replayState` was `{kind, version, api, provider, model, responseId, stopReason,
blocks}` when the reference corpus was written in August. The frozen v0
disposition allows `{response, blocks?}`, so `sessionFormatV0ToV1.migrate`
rejects the whole artifact.

It is an **optional** member of a finish chunk and carries no usage.
`strip-replaystate.mjs` drops it, and the v0 folds are bucket-for-bucket
identical before and after — that equality is the check that the strip is
accounting-neutral, and it is worth re-running rather than trusting.

State this before quoting any cross-version recomputation. A current DSH cannot
read a session log of that vintage, and the failure is a validation refusal
rather than a silent misread.

## The off-by-one worth knowing about

On v2 the inherited cut is the **seq of the marker itself**, so the marker is
the first event of the child's own work
(`session-format-v1-to-v2/src/codec.ts:125-136`, asserted as
`lastInheritedMarker === cut` in `validation.ts:88-127`). Adding one is wrong
and the totals will not tell you, because the marker carries no usage. The
self-test asserts the cut directly for this reason.
