#!/usr/bin/env bash
# Push the latest decision data to the always-on cloud viewer.
# Run this after you've run one or more local decision cycles.
#
#   ./sync.sh
#
# It commits the runs/ JSON (decisions + desk_state) and pushes to GitHub;
# Streamlit Community Cloud auto-redeploys within ~1 minute.
set -euo pipefail
cd "$(dirname "$0")"

git add runs/*.json 2>/dev/null || true
if git diff --cached --quiet; then
  echo "No new decision data to sync."
  exit 0
fi

git commit -m "data: sync desk decisions $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push
echo "Synced. The cloud viewer will refresh shortly."
