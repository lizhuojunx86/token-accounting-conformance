#!/usr/bin/env bash
# Run a tokscale binary over the fixture and say which key it is using.
#
#   ./run_check.sh /path/to/tokscale        # any build
#
# Cold cache, isolated HOME, DSH_HOME pinned at the fixture. Writes nothing
# outside $OUT and touches no real corpus.
set -uo pipefail

BIN="${1:?usage: run_check.sh /path/to/tokscale}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-/tmp/dsh-seq-key-check}"
HOME_DIR="$OUT/home"

mkdir -p "$OUT"
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"

env -i \
  HOME="$HOME_DIR" \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  DSH_HOME="$HERE/fixture" \
  TZ=UTC \
  "$BIN" --json > "$OUT/result.json" 2> "$OUT/result.err"
echo "[run] exit=$? bytes=$(wc -c < "$OUT/result.json")"

/usr/bin/python3 "$HERE/compare.py" \
  --result "$OUT/result.json" --expected "$HERE/fixture/expected.json"
