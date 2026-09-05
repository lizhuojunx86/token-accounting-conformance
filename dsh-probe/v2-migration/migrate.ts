// Convert the probe corpus from released v0 to released v2 using upstream's own
// migration packages, at whatever SHA this checkout is. Read-only on the source.
// Both migrations validate their target, so a refusal here is a real finding.

import { readFileSync, readdirSync, existsSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  releasedV0SessionFormatCodec,
  sessionFormatV0ToV1,
} from '@deepseek-ai/dsh-session-format-v0-to-v1'
import { sessionFormatV1ToV2 } from '@deepseek-ai/dsh-session-format-v1-to-v2'

const corpus = process.argv[2]
const out = process.argv[3]

const sessions: unknown[] = []
const root = join(corpus, 'sessions')

for (const proj of readdirSync(root)) {
  const projDir = join(root, proj)
  for (const sid of readdirSync(projDir)) {
    const f = join(projDir, sid, 'session.jsonl')
    if (!existsSync(f)) continue
    const lines = readFileSync(f, 'utf8').split('\n').filter(l => l.trim())
    const headerValue: unknown = JSON.parse(lines[0]!)
    const rowValues: unknown[] = lines.slice(1).map(l => JSON.parse(l))

    const v0 = releasedV0SessionFormatCodec.decodeArtifact(headerValue, rowValues)
    const v1 = sessionFormatV0ToV1.migrate(v0)
    sessionFormatV0ToV1.validateTarget(v1)
    const v2 = sessionFormatV1ToV2.migrate(v1)
    sessionFormatV1ToV2.validateTarget(v2)

    const id = (v2.header as Record<string, unknown>)['id']
    console.log(
      `${String(id)}  v0 events=${v0.events.length} cut=${v0.inheritedEventCount}` +
      `  ->  v1 events=${v1.events.length} cut=${v1.inheritedEventCount}` +
      `  ->  v2 events=${v2.events.length} cut=${v2.inheritedEventCount}`,
    )

    sessions.push({
      id,
      header: v2.header,
      inheritedEventCount: v2.inheritedEventCount,
      events: v2.events,
    })
  }
}

writeFileSync(out, JSON.stringify({ sessions }, null, 0))
console.log(`\nwrote ${out}`)
