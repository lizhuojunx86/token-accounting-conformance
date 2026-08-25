#!/usr/bin/env bash
# local helper: patch the /tmp clone, rebuild, restore the tree
set -uo pipefail
export PATH="$HOME/.cargo/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO=/tmp/ts1162
OUT=/tmp/ab1173
rm -f "$OUT/status2"
/usr/bin/python3 "$HERE/proposed.patch.py" "$REPO" || { echo FAIL > "$OUT/status2"; exit 1; }
cd "$REPO" || exit 1
if cargo build --release -p tokscale-cli > "$OUT/build2.log" 2>&1; then
  cp target/release/tokscale "$OUT/tokscale-cmp"
  echo DONE > "$OUT/status2"
else
  echo FAIL > "$OUT/status2"
fi
git checkout -- crates/tokscale-core/src/sessions/dsh.rs
