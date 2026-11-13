#!/bin/bash
# Weekly local job: collect usage totals from local agent logs and push them.
# Pushing data/usage.json triggers the GitHub Action that rebuilds the README.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

TOKEN="$(gh auth token --user cheree-agent-worker)"

git pull --rebase --quiet origin main || true

python3 -I scripts/collect_usage.py > data/usage.json.tmp
mv data/usage.json.tmp data/usage.json

git add data/usage.json
if git diff --cached --quiet; then
  echo "No change in usage.json"
  exit 0
fi

export GIT_AUTHOR_NAME="cheree-agent-worker"
export GIT_AUTHOR_EMAIL="356335772+cheree-agent-worker@users.noreply.github.com"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

git -c commit.gpgsign=false commit -q \
  -m "Update usage totals" \
  -m "Sent by a launchd job (scripts/weekly.sh), no model involved, on behalf of Chere3." \
  -m "Agent-Harness: launchd
Agent-Model: none
Agent-Role: scheduled job"

git -c credential.helper= \
    -c "credential.helper=!f() { echo username=cheree-agent-worker; echo password=$TOKEN; }; f" \
    push -q origin main
echo "Pushed usage totals"
