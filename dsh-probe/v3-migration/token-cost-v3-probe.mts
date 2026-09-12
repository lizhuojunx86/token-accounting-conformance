// Run dsh-token-cost's parser over a v3 corpus. usage:
//   TOKEN_COST_REPO=<clone of le-soleil-se-couche/dsh-token-cost> V3_CORPUS=<v3 sessions root> node --experimental-strip-types token-cost-v3-probe.mts
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
const S = process.env.V3_ROOT ?? process.cwd()
const { parseSessionLog } = await import((process.env.TOKEN_COST_REPO ?? S + '/dsh-token-cost') + '/src/parser.ts')
let corpus = 0
const root = process.env.V3_CORPUS ?? S + '/corpus-v3'
for (const proj of readdirSync(root)) for (const sid of readdirSync(join(root, proj))) {
  const f = join(root, proj, sid, 'session.jsonl')
  try {
    const parsed = parseSessionLog(readFileSync(f, 'utf8'), sid, '/x')
    const tot = parsed.records.reduce((s: number, r: any) => s + r.inputTokens + r.cacheReadTokens + r.cacheWriteTokens + r.outputTokens, 0)
    const comp = parsed.records.filter((r: any) => r.turn === -1).length
    corpus += tot
    console.log(sid.slice(-12), 'records', parsed.records.length, 'compaction', comp, 'total', tot)
  } catch (e) { console.log(sid.slice(-12), 'THROWS:', (e as Error).message) }
}
console.log('corpus total', corpus)
