#!/usr/bin/env bash
# Vendor the shared modules from zaptv-lib into the app directory.
#
# MPOS apps ship as flat .mpk packages, so shared code is copied in rather
# than installed. Edit the modules in the zaptv-lib repo, commit there, then
# re-run this; never edit the vendored copies in place. The commit taken is
# recorded in zaptv-lib.lock so a package can always be traced to it.
#
#   ./tools/sync-lib.sh            # from ../dev-zaptv-lib
#   ZAPTV_LIB=/path ./tools/sync-lib.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="${ZAPTV_LIB:-$ROOT/../dev-zaptv-lib}"
APP="$ROOT/org.zaptv.blocktv"
MODULES=(nostr_service.py zap_service.py market_data.py odometer.py field_picker.py)

[[ -d "$LIB/.git" ]] || { echo "zaptv-lib checkout not found at $LIB" >&2; exit 1; }
if [[ -n "$(git -C "$LIB" status --porcelain -- "${MODULES[@]}")" ]]; then
  echo "zaptv-lib has uncommitted changes to the modules; commit them first" >&2; exit 1
fi
for m in "${MODULES[@]}"; do cp "$LIB/$m" "$APP/$m"; done
rev=$(git -C "$LIB" rev-parse HEAD)
printf 'zaptv-lib %s\n%s\n' "$rev" "$(printf '%s\n' "${MODULES[@]}")" > "$ROOT/zaptv-lib.lock"
echo "vendored ${#MODULES[@]} modules from zaptv-lib @ ${rev:0:12}"
