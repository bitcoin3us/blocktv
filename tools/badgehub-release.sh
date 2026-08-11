#!/usr/bin/env bash
#
# Push a new release of an MPOS app to BadgeHub, without the web UI.
#
#   ./tools/badgehub-release.sh org.zaptv.app 0.3.1
#   ./tools/badgehub-release.sh org.zaptv.blocktv 0.2.0 --dry-run
#
# Requires a per-project API token. The project itself must already exist:
# BadgeHub's create-project endpoint is JWT-only and cannot be called with a
# token, so create it once in the browser, then use this for every update.
#
# The token is read from the macOS Keychain, or from BADGEHUB_TOKEN if set.
# Store it once with:
#
#   security add-generic-password -a "$USER" -s badgehub-<slug> -w
#
# (the -w with no value prompts, so the token never lands in shell history)

set -euo pipefail

API="https://badgehub.eu/api/v3"
DIST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/dist"

slug="${1:-}"
version="${2:-}"
dry_run=false
[[ "${3:-}" == "--dry-run" ]] && dry_run=true

if [[ -z "$slug" || -z "$version" ]]; then
  echo "usage: $0 <slug> <version> [--dry-run]" >&2
  exit 2
fi

mpk="$DIST/${slug}_${version}.mpk"
icon="$DIST/${slug}_${version}_64x64.png"
metadata="$DIST/${slug}_metadata.json"

for f in "$mpk" "$icon" "$metadata"; do
  if [[ ! -f "$f" ]]; then
    echo "missing: $f" >&2
    exit 1
  fi
done

# The version in metadata.json must match what we are publishing, otherwise
# the store shows one number and installs another.
meta_version=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$metadata")
if [[ "$meta_version" != "$version" ]]; then
  echo "version mismatch: metadata.json says $meta_version, publishing $version" >&2
  exit 1
fi

token="${BADGEHUB_TOKEN:-}"
if [[ -z "$token" ]]; then
  token=$(security find-generic-password -a "$USER" -s "badgehub-$slug" -w 2>/dev/null || true)
fi
if [[ -z "$token" ]]; then
  echo "no token: set BADGEHUB_TOKEN, or store one with" >&2
  echo "  security add-generic-password -a \"\$USER\" -s badgehub-$slug -w" >&2
  exit 1
fi

if $dry_run; then
  echo "dry run for $slug $version"
  echo "  mpk       $mpk ($(du -h "$mpk" | cut -f1))"
  echo "  icon      $icon"
  echo "  metadata  $metadata"
  echo "  token     found (${#token} chars)"
  echo "would PATCH metadata, upload both files, set icon sizes, then publish"
  exit 0
fi

# curl writes the HTTP status to stdout so a failure is never mistaken for
# success; -sS keeps it quiet but still prints real errors.
call() {
  local method="$1" path="$2"; shift 2
  local status
  status=$(curl -sS -o /tmp/badgehub-out.$$ -w '%{http_code}' \
    -X "$method" -H "badgehub-api-token: $token" "$API$path" "$@")
  if [[ "$status" -lt 200 || "$status" -ge 300 ]]; then
    echo "  FAILED ($status): $(head -c 400 /tmp/badgehub-out.$$)" >&2
    rm -f /tmp/badgehub-out.$$
    return 1
  fi
  rm -f /tmp/badgehub-out.$$
}

echo "metadata..."
call PATCH "/projects/$slug/draft/metadata" \
  -H "Content-Type: application/json" --data-binary "@$metadata"

echo "package..."
call POST "/projects/$slug/draft/files/$(basename "$mpk")" -F "file=@$mpk"

echo "icon..."
call POST "/projects/$slug/draft/files/$(basename "$icon")" -F "file=@$icon"
call POST "/projects/$slug/draft/icon" -H "Content-Type: application/json" \
  --data "{\"filePath\":\"$(basename "$icon")\",\"sizes\":[\"64x64\",\"32x32\",\"16x16\",\"8x8\"]}"

echo "publishing..."
call PATCH "/projects/$slug/publish"

echo "done: $slug $version is live at https://badgehub.eu/page/project/$slug"
