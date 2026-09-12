import { readFileSync } from 'node:fs'
import { sessionFormatCatalog } from '@deepseek-ai/dsh-session-format-catalog'
for (const f of process.argv.slice(2)) {
  const lines = readFileSync(f, 'utf8').split('\n').filter(l => l.trim())
  const r = sessionFormatCatalog.createRestore(JSON.parse(lines[0]!), { recovery: 'strict', validation: 'current' })
  for (const l of lines.slice(1)) r.decodeRow(JSON.parse(l))
  const a = r.finish()
  console.log(f.split('/').slice(-2, -1)[0]!.slice(-12), 'v' + a.header.version, 'events', a.events.length, 'cut', a.inheritedEventCount, 'OK')
}
