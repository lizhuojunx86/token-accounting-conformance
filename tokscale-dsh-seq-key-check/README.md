# tokscale DSH — what identifies a row that carries no `message.id`

[#1173](https://github.com/junhoyeo/tokscale/pull/1173) gave idless DSH rows a
`seq:` identity in the cross-file dedup key, so a fork whose header lost
`seedLength` can no longer bill a copied `compaction/summary` twice. The
Codex review on that PR objected that `seq` is unique only within a file, and
the PR merged without an answer.

This directory does not argue the probability. It points at the field that was
already in the event.

## The finding

Every `compaction/summary` carries `data.compactionId`, a per-call UUID, and a
fork copies it verbatim. Measured on a real four-session DSH corpus:

| | |
|---|---|
| usage-bearing rows | 39 |
| `assistant/message` taking the `msg:` branch | 36 |
| `compaction/summary` taking the `seq:` branch | 3 |
| summaries carrying `compactionId` | 3 of 3 |
| distinct `seq` values appearing in more than one file | 17 of 22 |

The last row is why the key rests almost entirely on the millisecond
timestamp: `seq` is dense from zero in every file, so it collides across files
as a matter of course rather than by coincidence.

The fork copy is verbatim. `session-001e8887`'s `compaction/start` at `seq
1007`, `time 1786781204037`, `compactionId 1ad33c8f-…` appears byte-identical
in its child `session-e61d64ec`. That is the same property `msg:{id}` already
relies on for assistant messages.

## The fixture

Two legs, one root, built by `build_fixture.py`, which derives the expected
totals by arithmetic before writing anything.

**Leg A** — two unrelated sessions, no `parentSession`, no `seedLength`, whose
summaries agree on `seq`, `time`, provider, model and every usage bucket, and
differ only in `compactionId`. Two separately billed summarize calls.

**Leg B** — a parent and a fork whose header lost `seedLength`, the child's
prefix repeating the parent's summary verbatim. One billed call. This is the
case `seq:` was added for and it must keep collapsing.

A `compactionId`-keyed identity satisfies both. A `seq`-keyed one satisfies
only B, and drops one of leg A's two calls.

## Measured

Cold cache, isolated `HOME`, `DSH_HOME` pinned at `fixture/`. `main` is
`6db1458e`; `+cmp:` is the same tree with `proposed.patch.py` applied, which is
one `.or_else` ahead of the existing one. Raw output in `results/`.

| field | main | +`cmp:` | correct |
|---|---|---|---|
| totalInput | 870 | 1,177 | 1,177 |
| totalOutput | 101 | 132 | 132 |
| totalCacheRead | 8,700 | 11,770 | 11,770 |
| totalCacheWrite | 16 | 23 | 23 |
| totalMessages | 6 | 7 | 7 |

Main lands on the `seq`-keyed prediction in every bucket, dropping one of leg
A's two summarize calls — 3,415 tokens. Leg B collapses correctly under both,
which is what says the change does not cost what #1173 bought.

On the real corpus the change is a no-op. Same two binaries, cold cache,
isolated `HOME` each:

```
main   input 66,094  output 8,281  cacheRead 249,728  cacheWrite 0  messages 22
+cmp:  input 66,094  output 8,281  cacheRead 249,728  cacheWrite 0  messages 22
```

Both land on the post-#1162 figures recorded in `../tokscale-dsh-compaction-check/results/`.
Every summary there reaches `cmp:` instead of `seq:` and nothing moves, because
none of them were colliding.

## Running it

```sh
python3 build_fixture.py          # regenerates the fixture; re-running is a no-op
./run_check.sh /path/to/tokscale  # any build
```

`run_check.sh` runs the binary with a cold isolated `HOME` and `DSH_HOME`
pinned at `fixture/`. `compare.py` prints the measured totals beside both
predictions and names which key the binary used, rather than only passing or
failing.

`key_entropy.py --root <sessions>` and `headers.py --root <sessions>` are the
read-only probes behind the table above. Point them at a real corpus; they
write nothing.

## What this does not claim

Leg A is **constructed**. Nothing in the measured corpus shows two unrelated
sessions agreeing on a millisecond timestamp, and this says nothing about how
often that happens in the wild. The argument is not that the collision is
likely. It is that a globally unique id sits in the same event, at the same
cost, and using it removes the question instead of pricing it.

The corpus behind the table has four sessions on one machine and two
provider routes. Its forks all carry `parentSession` and `seedLength`
together, so the seedLength-less fork that leg B models is constructed too —
the double count #1173 fixes does not occur in this corpus at all.

`build_patched.sh` is how the `+cmp:` column was produced: it applies
`proposed.patch.py` to a tokscale checkout at `/tmp/ts1162`, builds
`tokscale-cli` in release, drops the binary in `/tmp/ab1173/tokscale-cmp` and
restores the tree with `git checkout --`. Edit the two paths if yours differ.
