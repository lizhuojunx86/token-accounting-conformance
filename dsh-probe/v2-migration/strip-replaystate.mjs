// The frozen released-v0 reader in the current tree accepts replayState only as
// {response, blocks?}. Logs written by 0.1.0-rc.6 carry a different shape, so the
// v0->v1 migration refuses the corpus. replayState is an OPTIONAL member of a
// finish chunk and carries no usage, so dropping it lets the corpus through
// without touching any number any fold reads.
//
// usage: node strip-replaystate.mjs <srcCorpus> <dstCorpus>

import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const [, , src, dst] = process.argv
let stripped = 0
let lines = 0

const srcRoot = join(src, 'sessions')
for (const proj of readdirSync(srcRoot)) {
  for (const sid of readdirSync(join(srcRoot, proj))) {
    const f = join(srcRoot, proj, sid, 'session.jsonl')
    if (!existsSync(f)) continue
    const outDir = join(dst, 'sessions', proj, sid)
    mkdirSync(outDir, { recursive: true })
    const out = readFileSync(f, 'utf8').split('\n').filter(l => l.trim()).map(l => {
      const o = JSON.parse(l)
      lines++
      if (o?.data?.chunk?.replayState !== undefined) { delete o.data.chunk.replayState; stripped++ }
      if (o?.data?.message?.source?.replayState !== undefined) { delete o.data.message.source.replayState; stripped++ }
      return JSON.stringify(o)
    })
    writeFileSync(join(outDir, 'session.jsonl'), out.join('\n') + '\n')
  }
}
console.log(`lines=${lines} replayState removed=${stripped}`)
