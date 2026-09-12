// Restore any released-format DSH session corpus (v0..v3) to the current
// format through the shipped catalog (the same path persistence uses to open an
// old log), write the current-format session.jsonl, and fold the events four
// ways with the REAL tokenUsageProjectionDefinition from token-meter.
//
//   N  naive      every usage sighting summed (chunk + settlement usage +
//                 embedded stream last-usage + compaction)
//   O  official   tokenUsageProjectionDefinition.apply over every event
//   S  seed-aware official, but starting at the inherited cut
//   C  correct    S + compaction/summary.usage
//
// usage: pnpm exec tsx scripts/probe/emit-v3.ts <sessionsRoot> [<dstRoot>] [--json out.json] [--target 2|3]
//   <sessionsRoot> contains <project>/<sessionId>/session.jsonl
//   <dstRoot>      optional; current-format logs are written to <dstRoot>/<project>/<sessionId>/session.jsonl

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { sessionFormatCatalog as catalogV3 } from '@deepseek-ai/dsh-session-format-catalog'
import { createSessionFormatCatalog } from '@deepseek-ai/dsh-session-format'
import { releasedV0SessionFormatCodec, releasedV1SessionFormatCodec, sessionFormatV0ToV1 } from '@deepseek-ai/dsh-session-format-v0-to-v1'
import { releasedV2SessionFormatCodec, sessionFormatV1ToV2 } from '@deepseek-ai/dsh-session-format-v1-to-v2'
import { tokenUsageProjectionDefinition } from '../../packages/llm/token-meter/src/usage-projection.ts'

type Buckets = { uncachedInputTokens: number, outputTokens: number, cacheReadTokens: number, cacheWriteTokens: number }
const zero = (): Buckets => ({ uncachedInputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 })
const fromUsage = (u: any): Buckets => ({
  uncachedInputTokens: u?.inputTokens ?? 0,
  outputTokens: u?.outputTokens ?? 0,
  cacheReadTokens: u?.cacheReadTokens ?? 0,
  cacheWriteTokens: u?.cacheWriteTokens ?? 0,
})
const add = (a: Buckets, b: Buckets): Buckets => ({
  uncachedInputTokens: a.uncachedInputTokens + b.uncachedInputTokens,
  outputTokens: a.outputTokens + b.outputTokens,
  cacheReadTokens: a.cacheReadTokens + b.cacheReadTokens,
  cacheWriteTokens: a.cacheWriteTokens + b.cacheWriteTokens,
})
const total = (b: Buckets) => b.uncachedInputTokens + b.outputTokens + b.cacheReadTokens + b.cacheWriteTokens

function lastStreamUsage(stream: unknown): any {
  if (!Array.isArray(stream)) return undefined
  let u: any
  for (const m of stream) {
    if (m && typeof m === 'object' && (m as any).type === 'chunk' && (m as any).chunk?.type === 'usage') u = (m as any).chunk.usage
  }
  return u
}

function official(events: readonly any[]): Buckets {
  let state = tokenUsageProjectionDefinition.init()
  for (const e of events) state = tokenUsageProjectionDefinition.apply(state, e as any)
  return state.totals
}

function naive(events: readonly any[]): { buckets: Buckets, sightings: number } {
  let b = zero(); let n = 0
  for (const e of events) {
    const d = e.data ?? {}
    if (e.type === 'assistant/chunk' && d.chunk?.type === 'usage') { b = add(b, fromUsage(d.chunk.usage)); n++ }
    if (e.type === 'assistant/message' || e.type === 'assistant/attempt') {
      if (d.usage !== undefined) { b = add(b, fromUsage(d.usage)); n++ }
      const su = lastStreamUsage(d.stream)
      if (su !== undefined) { b = add(b, fromUsage(su)); n++ }
    }
    if (e.type === 'compaction/summary' && d.usage !== undefined) { b = add(b, fromUsage(d.usage)); n++ }
  }
  return { buckets: b, sightings: n }
}

function compaction(events: readonly any[]): { buckets: Buckets, count: number } {
  let b = zero(); let c = 0
  for (const e of events) if (e.type === 'compaction/summary' && e.data?.usage !== undefined) { b = add(b, fromUsage(e.data.usage)); c++ }
  return { buckets: b, count: c }
}

const args = process.argv.slice(2)
const jsonIdx = args.indexOf('--json')
const jsonOut = jsonIdx >= 0 ? args[jsonIdx + 1] : undefined
const targetIdx = args.indexOf('--target')
const targetVersion = targetIdx >= 0 ? Number(args[targetIdx + 1]) : 3
const positional = args.filter((a, i) => !(jsonIdx >= 0 && (i === jsonIdx || i === jsonIdx + 1)) && !(targetIdx >= 0 && (i === targetIdx || i === targetIdx + 1)))
// --target 2 stops the chain at released v2 (same accounting grammar as v3), for
// corpora the v2->v3 stage refuses. Built from the same shipped codecs/migrations.
const catalogV2 = createSessionFormatCatalog({
  currentVersion: 2,
  codecs: [releasedV0SessionFormatCodec, releasedV1SessionFormatCodec, releasedV2SessionFormatCodec],
  currentEncoder: releasedV2SessionFormatCodec,
  migrations: [sessionFormatV0ToV1, sessionFormatV1ToV2],
  restoreCurrent: a => a,
  restoreTransformedCurrent: a => a,
  restoreCurrentHeader: h => h,
})
const sessionFormatCatalog = targetVersion === 2 ? catalogV2 : catalogV3
const [srcRoot, dstRoot] = positional
if (!srcRoot) { console.error('usage: emit-v3.ts <sessionsRoot> [<dstRoot>] [--json out.json]'); process.exit(2) }

const report: any[] = []
const corpus = { N: zero(), O: zero(), S: zero(), C: zero(), sightings: 0, compactionEvents: 0, events: 0, sessions: 0 }

for (const proj of readdirSync(srcRoot)) {
  const projDir = join(srcRoot, proj)
  let sids: string[]
  try { sids = readdirSync(projDir) } catch { continue }
  for (const sid of sids) {
    const f = join(projDir, sid, 'session.jsonl')
    if (!existsSync(f)) continue
    const lines = readFileSync(f, 'utf8').split('\n').filter(l => l.trim())
    const headerValue = JSON.parse(lines[0]!)
    const read = sessionFormatCatalog.readHeader(headerValue)
    if (read.status === 'unsupported' || read.status === 'malformed') {
      console.log(`${proj}/${sid}: ${read.status}: ${read.reason}`)
      report.push({ project: proj, session: sid, status: read.status, reason: read.reason })
      continue
    }
    let art
    try {
      const restore = sessionFormatCatalog.createRestore(headerValue, { recovery: 'strict', validation: (process.env.DSH_VALIDATION as any) ?? 'transformed' })
      for (const l of lines.slice(1)) restore.decodeRow(JSON.parse(l))
      art = restore.finish()
    } catch (error: unknown) {
      const reason = error instanceof Error ? error.message : String(error)
      console.log(`${proj}/${sid}: v${read.storedVersion} REFUSED by restore to v${sessionFormatCatalog.currentVersion}: ${reason}`)
      report.push({ project: proj, session: sid, storedVersion: read.storedVersion, status: 'refused', reason })
      continue
    }
    const events = art.events
    const cut = art.inheritedEventCount

    if (dstRoot) {
      const outDir = join(dstRoot, proj, sid)
      mkdirSync(outDir, { recursive: true })
      const rows = [
        JSON.stringify(sessionFormatCatalog.encodeCurrentHeader(art.header, cut)),
        ...events.map(e => JSON.stringify(sessionFormatCatalog.encodeCurrentEvent(e))),
      ]
      writeFileSync(join(outDir, 'session.jsonl'), rows.join('\n') + '\n')
    }

    const N = naive(events)
    const O = official(events)
    const own = events.slice(cut)
    const S = official(own)
    const comp = compaction(own)
    const C = add(S, comp.buckets)
    const row = {
      project: proj, session: sid, storedVersion: read.storedVersion, targetVersion: read.targetVersion,
      events: events.length, inheritedCut: cut, isSeeded: (art.header as any).isSeeded ?? undefined,
      sightings: N.sightings, compactionEvents: comp.count,
      N: total(N.buckets), O: total(O), S: total(S), C: total(C),
      buckets: { N: N.buckets, O, S, C, compaction: comp.buckets },
    }
    report.push(row)
    corpus.N = add(corpus.N, N.buckets); corpus.O = add(corpus.O, O); corpus.S = add(corpus.S, S); corpus.C = add(corpus.C, C)
    corpus.sightings += N.sightings; corpus.compactionEvents += comp.count; corpus.events += events.length; corpus.sessions++
    console.log(`${proj}/${sid}  v${read.storedVersion}->v${read.targetVersion}  events=${events.length}  cut=${cut}  sightings=${N.sightings}  N=${row.N}  O=${row.O}  S=${row.S}  C=${row.C}  compaction=${comp.count}/${total(comp.buckets)}`)
  }
}

const summary = {
  sessions: corpus.sessions, events: corpus.events, sightings: corpus.sightings, compactionEvents: corpus.compactionEvents,
  N: total(corpus.N), O: total(corpus.O), S: total(corpus.S), C: total(corpus.C),
  'C-S (compaction missed)': total(corpus.C) - total(corpus.S),
  'O-S (fork prefix)': total(corpus.O) - total(corpus.S),
  buckets: corpus,
  tokenUsageStateVersion: tokenUsageProjectionDefinition.stateVersion,
  currentFormatVersion: sessionFormatCatalog.currentVersion,
}
console.log('\ncorpus:', JSON.stringify({ ...summary, buckets: undefined }, null, 2))
if (jsonOut) writeFileSync(jsonOut, JSON.stringify({ summary, sessions: report }, null, 2) + '\n')
