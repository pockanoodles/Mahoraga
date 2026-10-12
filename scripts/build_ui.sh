#!/usr/bin/env bash
# Build the web UI and put it where the Python package serves it from.
#
# The UI is never committed, so a wheel is only as good as the last run of this
# script before `python -m build`. The release workflow, the CI package job and
# the Dockerfile all call it; run it yourself before building a wheel by hand.
#
#   scripts/build_ui.sh      ->  backend/orchestrator/service/ui/
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$root/backend/orchestrator/service/ui"

cd "$root/frontend"
npm ci
npm run build

if [ ! -f dist/index.html ]; then
  echo "build_ui: frontend/dist/index.html is missing after the build" >&2
  exit 1
fi

rm -rf "$dest"
mkdir -p "$dest"
cp -R dist/. "$dest/"
# Source maps only help when debugging the UI in dev; they roughly double the
# size of the wheel.
find "$dest" -name '*.map' -delete

echo "build_ui: copied the UI to backend/orchestrator/service/ui/"
