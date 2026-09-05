#!/usr/bin/env bash
# Four-leg gate for tokscale #1235 (DSH summaries keyed by compactionId),
# run on the published npm packages rather than a build.
#
#   ./gate_published.sh <old-binary> <new-binary> [corpus DSH_HOME]
#
#   old = the last release keyed by seq  (tokscale@4.15.0, parser identity 4)
#   new = the first release keyed by cmp (tokscale@4.15.1, parser identity 5)
#
# Legs, per root (the fixture, and a real corpus if one is given):
#   A  old, cold      fresh HOME_A
#   B  new, warm      HOME_A again — the cache old wrote, read by new. The
#                     migration leg: an identity bump must reparse seq-keyed
#                     rows rather than serve them.
#   C  new, cold      fresh HOME_C
#   D  new, warm      HOME_C again (idempotence)
#
# Writes only under $OUT. Reads the corpus, never writes to it.
set -uo pipefail
OLD="${1:?usage: gate_published.sh <old-binary> <new-binary> [corpus DSH_HOME]}"
NEW="${2:?usage: gate_published.sh <old-binary> <new-binary> [corpus DSH_HOME]}"
CORPUS="${3:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-/tmp/dsh-seq-key-gate}"
mkdir -p "$OUT"

run() { # name bin home root
  local name="$1" bin="$2" home="$3" root="$4"
  mkdir -p "$home"
  env -i HOME="$home" PATH=/usr/bin:/bin:/usr/sbin:/sbin DSH_HOME="$root" TZ=UTC \
    "$bin" --json > "$OUT/$name.json" 2> "$OUT/$name.err"
  echo "[$name] exit=$? bytes=$(wc -c < "$OUT/$name.json" | tr -d ' ')"
}

legs() { # root_name root
  local root_name="$1" root="$2"
  rm -rf "$OUT/home-$root_name-A" "$OUT/home-$root_name-C"
  run "$root_name-A-old-cold"  "$OLD" "$OUT/home-$root_name-A" "$root"
  run "$root_name-B-new-warm"  "$NEW" "$OUT/home-$root_name-A" "$root"
  run "$root_name-C-new-cold"  "$NEW" "$OUT/home-$root_name-C" "$root"
  run "$root_name-D-new-warm2" "$NEW" "$OUT/home-$root_name-C" "$root"
  # After leg B, HOME_A's cache should be what a cold new run writes.
  local a="$OUT/home-$root_name-A/.config/tokscale/cache/source-message-cache-v2/dsh"
  local c="$OUT/home-$root_name-C/.config/tokscale/cache/source-message-cache-v2/dsh"
  if [ -d "$a" ] && [ -d "$c" ]; then
    for f in "$a"/*.bin; do
      b="$(basename "$f")"
      if cmp -s "$f" "$c/$b"; then echo "[$root_name] $b: A-after-B == C-cold"; else echo "[$root_name] $b: DIFFERS"; fi
    done
  fi
}

echo "old: $("$OLD" --version 2>/dev/null)  sha256 $(shasum -a 256 "$OLD" | cut -c1-16)"
echo "new: $("$NEW" --version 2>/dev/null)  sha256 $(shasum -a 256 "$NEW" | cut -c1-16)"
legs fixture "$HERE/fixture"
[ -n "$CORPUS" ] && legs corpus "$CORPUS"

/usr/bin/python3 - "$OUT" <<'PY'
import json, glob, os, sys
out = sys.argv[1]
print("%-24s %10s %10s %12s %10s %8s" % ("leg", "input", "output", "cacheRead", "cacheWrite", "messages"))
for p in sorted(glob.glob(out + "/*.json")):
    name = os.path.basename(p)[:-5]
    try:
        d = json.load(open(p))
        dsh = [e for e in d.get("entries", []) if e.get("client") == "dsh"]
        agg = {k: sum(e[k] for e in dsh) for k in ("input", "output", "cacheRead", "cacheWrite", "messageCount")}
        print("%-24s %10d %10d %12d %10d %8d" % (name, agg["input"], agg["output"], agg["cacheRead"], agg["cacheWrite"], agg["messageCount"]))
    except Exception as ex:
        print(name, "UNPARSEABLE", ex)
PY
