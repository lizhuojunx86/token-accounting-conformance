// Re-fold the DSH probe corpus under v0/v1 and v2 event models.
// Read-only. Prints bucket totals for each fold so v0 and v2 can be compared
// on identical ground truth.
//
// usage: node fold.mjs v0 <corpusDir>
//        node fold.mjs v2 <migratedJson>

import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const BUCKETS = ['inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens']

const zero = () => ({ inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 })
const add = (a, b) => { for (const k of BUCKETS) a[k] += b?.[k] ?? 0; return a }
const sub = (a, b) => { for (const k of BUCKETS) a[k] -= b?.[k] ?? 0; return a }
const eq = (a, b) => BUCKETS.every(k => (a?.[k] ?? 0) === (b?.[k] ?? 0))
const total = a => BUCKETS.reduce((s, k) => s + (a[k] ?? 0), 0)
const norm = u => { const o = zero(); for (const k of BUCKETS) o[k] = u?.[k] ?? 0; return o }

// ---------- session loading ----------

function loadV0(dir) {
  const out = []
  const root = join(dir, 'sessions')
  for (const proj of readdirSync(root)) {
    const projDir = join(root, proj)
    for (const sid of readdirSync(projDir)) {
      const f = join(projDir, sid, 'session.jsonl')
      if (!existsSync(f)) continue
      const lines = readFileSync(f, 'utf8').split('\n').filter(l => l.trim())
      const header = JSON.parse(lines[0])
      const events = lines.slice(1).map(l => JSON.parse(l))
      out.push({ id: header.id, header, events })
    }
  }
  return out.sort((a, b) => a.id.localeCompare(b.id))
}

function loadV2(file) {
  const raw = JSON.parse(readFileSync(file, 'utf8'))
  return raw.sessions.sort((a, b) => a.id.localeCompare(b.id))
}

// ---------- inherited cut ----------

// v0/v1: numeric seedLength on the header, counted in events.
// v2: header.isSeeded plus the last `session/end-seed {inherited:true}` marker.
// Upstream sets cut = the marker's own seq (codec.ts deriveInheritedEventCount,
// validation.ts lastInheritedMarker === cut), so the marker is the FIRST
// non-inherited event and events[0..cut-1] are the inherited prefix.
function inheritedCut(session, format) {
  if (format === 'v0') return session.header.seedLength ?? 0
  if (!session.header.isSeeded) return 0
  let cut = 0
  session.events.forEach((e, i) => {
    if (e.type === 'session/end-seed' && e.data?.inherited === true) cut = i
  })
  return cut
}

// ---------- usage extraction ----------

function lastStreamUsage(stream) {
  if (!Array.isArray(stream)) return undefined
  for (let i = stream.length - 1; i >= 0; i--) {
    const m = stream[i]
    const chunk = m?.chunk ?? m
    if (chunk?.type === 'usage') return chunk.usage
  }
  return undefined
}

// Every distinct place a usage number is written, per event.
// v0: one sighting per assistant/chunk usage event, one per assistant/message.
// v2: an assistant/message can carry BOTH data.usage and a stream usage chunk.
function sightings(event, format) {
  const out = []
  if (format === 'v0') {
    if (event.type === 'assistant/chunk' && event.data?.chunk?.type === 'usage') {
      out.push({ where: 'chunk', usage: event.data.chunk.usage })
    }
    if (event.type === 'assistant/message' && event.data?.usage !== undefined) {
      out.push({ where: 'message.usage', usage: event.data.usage })
    }
    return out
  }
  if (event.type === 'assistant/message') {
    if (event.data?.usage !== undefined) out.push({ where: 'message.usage', usage: event.data.usage })
    const s = lastStreamUsage(event.data?.stream)
    if (s !== undefined) out.push({ where: 'message.stream', usage: s })
  }
  if (event.type === 'assistant/attempt') {
    const s = lastStreamUsage(event.data?.stream)
    if (s !== undefined) out.push({ where: 'attempt.stream', usage: s })
  }
  return out
}

// What the official projection folds: one value per settlement.
function officialUsageOf(event, format) {
  if (format === 'v0') {
    if (event.type === 'assistant/chunk' && event.data?.chunk?.type === 'usage') return event.data.chunk.usage
    if (event.type === 'assistant/message' && event.data?.usage !== undefined) return event.data.usage
    return undefined
  }
  // v2 usageOf(): message.usage first, else last usage chunk in the embedded stream.
  if (event.type === 'assistant/message' && event.data?.usage !== undefined) return event.data.usage
  if (event.type !== 'assistant/message' && event.type !== 'assistant/attempt') return undefined
  return lastStreamUsage(event.data?.stream)
}

// ---------- folds ----------

// D-1 exhibit: sum every sighting, no dedup.
function naiveFold(sessions, format, { skipInherited }) {
  const t = zero()
  let count = 0
  for (const s of sessions) {
    const cut = skipInherited ? inheritedCut(s, format) : 0
    s.events.slice(cut).forEach(e => {
      for (const sight of sightings(e, format)) { add(t, norm(sight.usage)); count++ }
    })
  }
  return { totals: t, sightings: count }
}

// The shipped projection. retryAware=false reproduces stateVersion 1 (v0.1.0-rc.5),
// true reproduces stateVersion 2.
function officialFold(sessions, format, { retryAware }) {
  const t = zero()
  for (const s of sessions) {
    // The projection replays the whole log including any inherited prefix.
    let last = null
    for (const e of s.events) {
      if (e.type === 'llm/retry-started') { if (retryAware) last = null; continue }
      const sample = officialUsageOf(e, format)
      if (sample === undefined) continue
      const turn = e.data?.turn, step = e.data?.step
      const b = norm(sample)
      const prev = last && last.turn === turn && last.step === step ? last.buckets : undefined
      if (prev !== undefined && eq(prev, b)) continue
      if (prev !== undefined) sub(t, prev)
      add(t, b)
      last = { turn, step, buckets: b }
    }
  }
  return { totals: t }
}

// Corrected: skip the inherited prefix, one value per attempt, attempts add
// across a retried step, plus compaction/summary.usage.
function correctedFold(sessions, format) {
  const t = zero()
  const compaction = zero()
  let compactionEvents = 0
  let attempts = 0
  for (const s of sessions) {
    const cut = inheritedCut(s, format)
    let last = null
    for (const e of s.events.slice(cut)) {
      if (e.type === 'compaction/summary' && e.data?.usage !== undefined) {
        add(compaction, norm(e.data.usage)); compactionEvents++
        continue
      }
      if (e.type === 'llm/retry-started') { last = null; continue }
      const sample = officialUsageOf(e, format)
      if (sample === undefined) continue
      const turn = e.data?.turn, step = e.data?.step
      const b = norm(sample)
      const prev = last && last.turn === turn && last.step === step ? last.buckets : undefined
      if (prev !== undefined && eq(prev, b)) continue
      if (prev !== undefined) sub(t, prev)
      else attempts++
      add(t, b)
      last = { turn, step, buckets: b }
    }
  }
  const grand = add(add(zero(), t), compaction)
  return { assistant: t, compaction, compactionEvents, attempts, totals: grand }
}

// ---------- intra-event duplication probe (the v2 D-1 claim) ----------

function intraEventDuplication(sessions, format) {
  if (format === 'v0') return null
  let both = 0, agree = 0, disagree = []
  for (const s of sessions) {
    for (const e of s.events) {
      if (e.type !== 'assistant/message') continue
      const u = e.data?.usage
      const st = lastStreamUsage(e.data?.stream)
      if (u === undefined || st === undefined) continue
      both++
      if (eq(norm(u), norm(st))) agree++
      else disagree.push({ seq: e.seq, usage: norm(u), stream: norm(st) })
    }
  }
  return { both, agree, disagree }
}

// ---------- report ----------

const fmt = t => BUCKETS.map(k => `${k}=${t[k]}`).join(' ') + ` | total=${total(t)}`

const [, , mode, path] = process.argv
const format = mode === 'v0' ? 'v0' : 'v2'
const sessions = mode === 'v0' ? loadV0(path) : loadV2(path)

console.log(`=== format ${format} | ${sessions.length} sessions | ${sessions.reduce((n, s) => n + s.events.length, 0)} events ===`)
for (const s of sessions) {
  console.log(`  ${s.id}  events=${s.events.length}  inheritedCut=${inheritedCut(s, format)}`)
}

const naiveAll = naiveFold(sessions, format, { skipInherited: false })
const naiveOwn = naiveFold(sessions, format, { skipInherited: true })
const off1 = officialFold(sessions, format, { retryAware: false })
const off2 = officialFold(sessions, format, { retryAware: true })
const corr = correctedFold(sessions, format)

console.log(`\n[naive, all events]      sightings=${naiveAll.sightings}  ${fmt(naiveAll.totals)}`)
console.log(`[naive, own work only]   sightings=${naiveOwn.sightings}  ${fmt(naiveOwn.totals)}`)
console.log(`[official stateVersion1] ${fmt(off1.totals)}`)
console.log(`[official stateVersion2] ${fmt(off2.totals)}`)
console.log(`[corrected]              attempts=${corr.attempts} ${fmt(corr.totals)}`)
console.log(`  assistant portion      ${fmt(corr.assistant)}`)
console.log(`  compaction portion     events=${corr.compactionEvents} ${fmt(corr.compaction)}`)

const ratio = total(corr.totals) === 0 ? NaN : total(naiveAll.totals) / total(corr.totals)
const ratioVsAssistant = total(corr.assistant) === 0 ? NaN : total(naiveOwn.totals) / total(corr.assistant)
console.log(`\nD-1 naive(all)/corrected            = ${ratio.toFixed(6)}`)
console.log(`D-1 naive(own)/corrected-assistant  = ${ratioVsAssistant.toFixed(6)}`)

const dup = intraEventDuplication(sessions, format)
if (dup) {
  console.log(`\nD-1 v2 intra-event: assistant/message carrying BOTH usage and a stream usage chunk = ${dup.both}`)
  console.log(`  identical numbers in both places = ${dup.agree}`)
  if (dup.disagree.length) console.log(`  DISAGREE = ${JSON.stringify(dup.disagree.slice(0, 5), null, 2)}`)
}

console.log(`\nD-3 compaction/summary usage counted by the official fold: ${
  sessions.some(s => s.events.some(e => e.type === 'compaction/summary')) ? 'no (usageOf never matches it)' : 'n/a'
}`)

// D-2: per-seeded-session inflation. What the official fold reports for a fork
// (it replays the inherited prefix) against what that fork actually spent.
console.log(`\nD-2 fork inflation`)
for (const s of sessions) {
  const cut = inheritedCut(s, format)
  if (cut === 0) continue
  const reported = officialFold([s], format, { retryAware: true }).totals
  const own = correctedFold([s], format).assistant
  console.log(
    `  ${s.id}  cut=${cut}/${s.events.length}` +
    `  reported=${total(reported)}  own=${total(own)}` +
    `  inflation=${(total(reported) / total(own)).toFixed(2)}x`,
  )
}
