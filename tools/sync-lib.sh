#!/usr/bin/env bash
# Vendor the shared modules from zaptv-lib into the app directory.
#
# MPOS apps ship as flat .mpk packages, so shared code is copied in rather
# than installed. Edit the modules in the zaptv-lib repo, commit there, then
# re-run this; never edit the vendored copies in place. The commit taken is
# recorded in zaptv-lib.lock so a package can always be traced to it.
#
# All MPOS apps share one sys.modules, so a module imported under a name
# another app already used is that app's copy. Each module is vendored as
# blocktv_<name>.py, with the imports between them rewritten to match.
#
#   ./tools/sync-lib.sh            # from ../dev-zaptv-lib
#   ZAPTV_LIB=/path ./tools/sync-lib.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="${ZAPTV_LIB:-$ROOT/../dev-zaptv-lib}"
APP="$ROOT/org.zaptv.blocktv"
PREFIX=blocktv_
MODULES=(nostr_service.py zap_service.py market_data.py odometer.py field_picker.py clankertv_core.py clankertv_providers.py)

[[ -d "$LIB/.git" ]] || { echo "zaptv-lib checkout not found at $LIB" >&2; exit 1; }
if [[ -n "$(git -C "$LIB" status --porcelain -- "${MODULES[@]}")" ]]; then
  echo "zaptv-lib has uncommitted changes to the modules; commit them first" >&2; exit 1
fi
python3 - "$LIB" "$APP" "$PREFIX" "${MODULES[@]}" <<'PY'
import re
import sys

lib, app, prefix, modules = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
names = "|".join(m[:-3] for m in modules)
from_re = re.compile(r"^(\s*)from (%s) import " % names, re.M)
import_re = re.compile(r"^(\s*)import (%s)(?: as (\w+))?([ \t]*(?:#.*)?)$" % names, re.M)
left_re = re.compile(r"^\s*(?:from|import) (?:%s)\b" % names, re.M)
for m in modules:
    with open(lib + "/" + m) as f:
        src = f.read()
    src = from_re.sub(lambda g: g[1] + "from " + prefix + g[2] + " import ", src)
    src = import_re.sub(lambda g: g[1] + "import " + prefix + g[2] + " as " + (g[3] or g[2]) + g[4], src)
    left = left_re.findall(src)
    if left:
        sys.exit("%s: unrewritten import(s) %s" % (m, left))
    with open(app + "/" + prefix + m, "w") as f:
        f.write(src)
PY
rev=$(git -C "$LIB" rev-parse HEAD)
{
  printf 'zaptv-lib %s\n' "$rev"
  for m in "${MODULES[@]}"; do printf '%s -> %s%s\n' "$m" "$PREFIX" "$m"; done
} > "$ROOT/zaptv-lib.lock"
echo "vendored ${#MODULES[@]} modules from zaptv-lib @ ${rev:0:12} as ${PREFIX}*"
