# Who runs these invariants

2026-08-28 · companion to [`CONFORMANCE.md`](CONFORMANCE.md) (Claude Code, I-1..I-11)
and [`CONFORMANCE-DSH.md`](CONFORMANCE-DSH.md) (DeepSeek Harness, D-1..D-5)

No one has said "I adopted your catalog." As far as I can tell nobody cites
either file by name, and I'd rather open with that than let a page titled
*adopters* imply otherwise.

What does exist is narrower and more useful: places where one of these
invariants is now enforced by somebody else's code, and people who produced
the same number without sharing any code with me. Those are two different
claims and they're kept in two different tables below. Every row carries a
link, so each one can be checked rather than taken from me.

---

## 1 · Enforced in someone else's code

An invariant is in this table when the upstream project ships a change that
holds it. The fix is theirs; the measurement was mine.

| invariant | project | what landed | when |
|---|---|---|---|
| I-1 count each message once | Clawdmeter | three call sites collapsed by `message.id`, v3.0.1 — [#21](https://github.com/weltern/Clawdmeter/issues/21) | 2026-08-08 |
| I-2 collapse under per-bucket max | Clawdmeter | same exchange: weltern passed back the running-total shape on sidechain records, which is where I-2 and I-3 come from | 2026-08-08 |
| I-4 never sum streaming snapshots | splitrail | [#222](https://github.com/Piebald-AI/splitrail/pull/222), written by NickAme03 against the ratios I'd predicted | 2026-07-31 |
| I-5 walk the tree recursively | splitrail | [#209](https://github.com/Piebald-AI/splitrail/pull/209), "Include Claude Code subagent transcripts", written by mike1858 after the report | 2026-07 |
| I-6 no estimate on an API number | tokscale | [#1037](https://github.com/junhoyeo/tokscale/pull/1037) by Yuxin-Qiao, shipped v4.11.0 | 2026-08-06 |
| I-7 a re-read must not lower history | tokscale | v4.9.0, from [#994](https://github.com/junhoyeo/tokscale/issues/994) | 2026-08 |
| I-7 | viberank | [#111](https://github.com/sculptdotfun/viberank/pull/111) | 2026-08 |
| I-8 scope counters to their period | viberank | [#121](https://github.com/sculptdotfun/viberank/pull/121), per-month `{files, bytes}` | 2026-08 |
| I-9 absence is not an observation | viberank | [#124](https://github.com/sculptdotfun/viberank/pull/124), server-side | 2026-08 |
| I-10 a verdict must not outrun its evidence | viberank | [#143](https://github.com/sculptdotfun/viberank/pull/143) (`15da384`), contributions keyed per `(machine, agent)` via `ccusage --by-agent`; a split is kept only when it reconciles with the day it divides, and a Claude verdict now lowers Claude alone | 2026-08-22 |
| D-3 count the compaction call | tokscale (DSH parser) | [#1162](https://github.com/junhoyeo/tokscale/pull/1162) (`d97a829`), `"assistant/message" \| "compaction/summary"` arm at `sessions/dsh.rs:153`, parser version 1→2. Fixed before the DSH client ever shipped | 2026-08-22 |

## 2 · Regression tests other people wrote

This is the form that survives me. A measurement I post decays; a test in
their tree fails on its own.

- **tokscale [#1139](https://github.com/junhoyeo/tokscale/pull/1139)** —
  junhoyeo seeds a cache entry the current parser cannot produce, asserts the
  seeded cache really is inflated, then scans. Disabling the rebuild fails it
  at `left: 110, right: 100`. It pins the cache half of I-6, which is the half
  that was still open. Merged 2026-08-17.
- **tokscale [#1162](https://github.com/junhoyeo/tokscale/pull/1162)** — four
  DSH tests for D-3, plus a non-vacuity check: with the `compaction/summary`
  arm reverted, three of the four fail. Merged 2026-08-22 (`d97a829`). Two of
  the four cover cases my corpus cannot reach — a summary carrying no usage,
  and a summary inside a forked seed prefix.
- **viberank [#121](https://github.com/sculptdotfun/viberank/pull/121)** — the
  per-month scope caught its own first implementation dropping 17 of 922
  files. The structure made a 2% undercount legible; that is the argument for
  the structure.

## 3 · Independent reproductions

Nobody here works from my code. Where a number matches, two implementations
sharing nothing landed on it — which is the only reason any of this is
checkable.

| who | what | where |
|---|---|---|
| yha9806 | implemented D-3 + D-4 on a fork (`63688b0`): folds `compaction/summary.usage`, treats error/aborted finish chunks as attempt boundaries, bumps `stateVersion` 1→2. I replayed it against my corpus: 308,234 whole-log, 55,886 child-own, bucket for bucket | [deepseek-harness#1886](https://github.com/deepseek-ai/deepseek-harness/discussions/1886) |
| hydraxman | produced the same patch independently, ran the official suite (23 tests, Node 22.19.0), and added the version-1 checkpoint argument I had not stated | same thread |
| le-soleil-se-couche | a fully synthetic fixture covering D-1..D-4, fork seed and cache-write traffic — no private logs needed. Also produced the counterexample that **corrected me**: generalising the attempt boundary to any `failure` field would double-count, because AgentLoop still allowlists `error \| aborted` | same thread |
| aron-intframe | reported a viberank double-count in the same shape, self-measured: a public $150.5K / 108.9B against a real $91.3K / 70.8B. Maintainer verified and repaired it | [viberank#127](https://github.com/sculptdotfun/viberank/issues/127) |
| NickAme03 | filed the splitrail I-4 case ([#220](https://github.com/Piebald-AI/splitrail/issues/220)) whose ratios I'd predicted and then verified, wrote the fix himself, and has since carried the same class elsewhere — "Streaming rows are not duplicates: keeping the first one undercounts" | [ccseva#38](https://github.com/Iamshankhadeep/ccseva/issues/38) |
| a137460387 | a fourth implementation of D-3, as one commit on top of master, with unit coverage and a matching Web fixture. Confirms the gap is still live on `b150a551b8` (`dsh-v0.1.1-rc.2`) and deliberately leaves D-4 alone, for the same reason yha9806 did: the attempt-boundary rule is the structural decision still open. Then folded `le-soleil-se-couche`'s fixture through it — the first numbers any of the four implementations had produced on that substrate — and **closed a gap I had been carrying since the opening post**: his compaction increment is +31 / +9 / +37 / **+6**, a summary reporting cache-write traffic, folded correctly. `cacheWriteTokens` on the compaction path is unobserved in the wild, but it is no longer untested | [deepseek-harness#1886](https://github.com/deepseek-ai/deepseek-harness/discussions/1886) |
| pinion05 | measured DSH model attribution on a live `~/.dsh` tree — 1,231 of 1,762 rows served by a model other than the configured one. Self-closed, but it is the second person reading `sessions/dsh.rs` for accounting | [tokscale#1163](https://github.com/junhoyeo/tokscale/pull/1163) |
| vpimshin | a fifth implementation (`fix/token-usage-compaction-and-retry`, `b4bbea2` + `e28f179`), the second written against current master and the first to change the logging side rather than only the fold: `compaction/end` gains `usage`/`provider`/`model`, so a failed or later-rejected summarize call's spend reaches the log at all. Covers the retry boundary too — but upstream had already landed that half two days *before* his post (`b565df344`, on the default branch 2026-08-25T11:09Z), keyed differently, which is where the two incompatible `stateVersion: 2`s below come from. Neither of us noticed: he measured against `b150a551b8` and I kept re-checking the same tag, and rc.2 is not a descendant of the fix. 480 tests self-reported; on my corpus all three `compaction/end` events carry neither error nor usage, so the schema half is fixture-only for now | [deepseek-harness#1886](https://github.com/deepseek-ai/deepseek-harness/discussions/1886#discussioncomment-18176363) |

## 4 · Conformance records

Somebody pointed `dsh-conformance/` at their own fold and posted what it said.
Distinct from §3: a reproduction agrees with a number of mine, a record is the
checker's verdict on code I have never seen.

| who | fold | verdict | where |
|---|---|---|---|
| le-soleil-se-couche | local `dsh-token-cost`, through a read-only adapter over the committed fixture. Node 22.22.1, Python 3.14.6, checker pinned at `0667479` | PASS on all four buckets, 1,050 / 105 / 10,500 / 13, covering D-1..D-4, plus the self-test | [deepseek-harness#1886](https://github.com/deepseek-ai/deepseek-harness/discussions/1886#discussioncomment-18141954) |
| a137460387 | the branch's own patched projection (`upstream-pr/token-meter-compaction-usage`, tip `64ee978`), folded per event through `init`/`apply`. Node v24.16.0, Windows Git Bash, Python 3.10, checker at `d1c44e5` | FAIL, as the branch's declared scope predicts: reported = the official fold plus the full compaction increment, and the residual 200 / 20 / 2,000 / 12 is `gap_inherited − gap_superseded` with no compaction term | [deepseek-harness#1886](https://github.com/deepseek-ai/deepseek-harness/discussions/1886#discussioncomment-18151717) |

One run, and it found a defect in the fixture on the first try: the session
directories were `session-conformance-{parent,child}` while the headers carried
`id: conformance-{parent,child}`. Real DSH cannot produce that, because the
transcript lives at `<DSH_HOME>/sessions/<encoded-cwd>/<session-id>/` and the
directory name is the session id. A fold taking the directory name as the id,
which is what the vendor parser does when the header is missing, failed on a
hazard that does not exist in the wild. `reference.py` reads `header["id"]` and
never touches the path, so the suite was structurally unable to catch this
itself. Fixed in `e211154`; every bucket and gap term unchanged, and
`expected.json` byte-identical.

He scoped the record himself, and it is worth carrying over rather than
paraphrasing: it covers the fold semantics, not a shipped CLI, and it settles
nothing about who owns the attempt-boundary decision upstream.

A second defect of the same class surfaced on 2026-08-28, this time with
nobody running anything: the fixture's `llm/retry` and `llm/retry-started`
markers carried no `turn`/`step`. `llm-retry` writes both with the attempt
they belong to (`append('llm/retry-started', { retryId, turn, step, retry })`,
`packages/llm/llm-retry/src/index.ts:152`) and its own invariant rejects a
started event whose turn/step does not match its scheduled attempt, so again
the fixture described a shape the wild never produces. It went unnoticed
because no fold read those events — until upstream's `b565df344` keyed the
attempt boundary on exactly them. Against the old fixture that fold scored
850 / 85 / 8,500 / 25, byte for byte the *unpatched* rc.2 result: the checker
would have reported an implementation that does fix D-4 as fixing none of it.
Both defects are the same failure mode, and `reference.py` was insulated from
both — it consumes no `llm/*` event either. Fixed in `e955166`; `expected.json`
byte-identical again, every fold and gap term unchanged.

The second run is the first FAIL, and the first from Windows. It is the verdict working as designed: the residual decomposes into exactly the two dimensions the branch deliberately leaves out, and it read the same from outside the checkout as it had from inside it. His one wiring note (the fold command must run with its cwd inside the branch checkout, or the TS loader falls back to an unbuilt `lib/`) is in the README now (`f74b4a4`).

---

## 5 · Offered and not taken up

Kept here so the page isn't only wins.

- **`ci/tokscale.yml`** — the drift harness as a drop-in workflow, offered at
  [tokscale#1011](https://github.com/junhoyeo/tokscale/issues/1011) and
  [Clawdmeter#21](https://github.com/weltern/Clawdmeter/issues/21). Not wired
  into any repo yet. The DSH checker has now been run by someone else
  (§4), but no project runs any of this in its own CI.
- **I-1 in claude-code-templates** —
  [#754](https://github.com/davila7/claude-code-templates/pull/754), open,
  checks green, no maintainer response. The last unshipped fix.
- **D-1..D-5 upstream** — half moved, at last. `b565df344` (2026-08-25,
  released in `dsh-v0.1.2-alpha.1` / `cd5ef814`) lands the retry half: `apply()` clears the replacement slot on
  `llm/retry-started` and `tokenUsage.stateVersion` goes 1 → 2. The compaction
  half is still missing (`usageOf()` has no `compaction/summary` branch at
  `cd5ef814`), and external PRs are still closed by CONTRIBUTING. The decay this
  section warned about happened twice over: `63688b0` already no longer applied
  after `4c421ec88`/`9127d7e8b`, and now there are two incompatible
  `stateVersion: 2`s in the wild — upstream keys the attempt boundary on
  `llm/retry-started`, vpimshin's branch on failure kind — so anything trusting
  that number to identify a persisted projection can be wrong while looking
  right. A fix nobody rules on does not keep; it goes stale against the API it
  was written for, and then the version number stops meaning one thing.

---

## Getting a row here

Two ways, and neither involves agreeing with me.

Hold one of the invariants in code — a test, a check, a workflow — and link
it. Or produce a number that contradicts one, in which case the catalog entry
is wrong and I'd rather find out from you than not. The harnesses here are
stdlib-only and run in about a minute; the DSH one ships its own fixture, so it
needs no corpus of yours.

If a row above is wrong about your project, open an issue or a PR against
this file.
