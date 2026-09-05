// Emit real released-v2 session.jsonl from a released-v0 corpus, using upstream's
// own migration and codec. The output is what a current DSH writes, not a
// hand-built approximation, so a reader tested against it is tested against the
// shipped physical layout.
//
// usage: tsx emit-v2.ts <srcCorpus> <dstRoot>

import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import {
  releasedV0SessionFormatCodec,
  sessionFormatV0ToV1,
} from '@deepseek-ai/dsh-session-format-v0-to-v1'
import {
  sessionFormatV1ToV2,
  releasedV2SessionFormatCodec,
} from '@deepseek-ai/dsh-session-format-v1-to-v2'

const [, , src, dstRoot] = process.argv
const srcRoot = join(src, 'sessions')

for (const proj of readdirSync(srcRoot)) {
  for (const sid of readdirSync(join(srcRoot, proj))) {
    const f = join(srcRoot, proj, sid, 'session.jsonl')
    if (!existsSync(f)) continue

    const lines = readFileSync(f, 'utf8').split('\n').filter(l => l.trim())
    const v0 = releasedV0SessionFormatCodec.decodeArtifact(
      JSON.parse(lines[0]!),
      lines.slice(1).map(l => JSON.parse(l)),
    )
    const v1 = sessionFormatV0ToV1.migrate(v0)
    sessionFormatV0ToV1.validateTarget(v1)
    const v2 = sessionFormatV1ToV2.migrate(v1)
    sessionFormatV1ToV2.validateTarget(v2)

    const encoded = releasedV2SessionFormatCodec.encodeArtifact(v2)

    // Round-trip through the decoder so the bytes we commit are bytes the
    // shipped reader accepts.
    const back = releasedV2SessionFormatCodec.decodeArtifact(encoded.header, encoded.rows)
    if (back.events.length !== v2.events.length) throw new Error(`${sid}: round-trip event count drift`)
    if (back.inheritedEventCount !== v2.inheritedEventCount) throw new Error(`${sid}: round-trip cut drift`)

    const outDir = join(dstRoot, 'sessions', proj, sid)
    mkdirSync(outDir, { recursive: true })
    const body = [JSON.stringify(encoded.header), ...encoded.rows.map(r => JSON.stringify(r))]
    writeFileSync(join(outDir, 'session.jsonl'), body.join('\n') + '\n')

    console.log(
      `${sid}  rows=${encoded.rows.length}  isSeeded=${String(encoded.header['isSeeded'])}` +
      `  cut=${back.inheritedEventCount}  round-trip ok`,
    )
  }
}
