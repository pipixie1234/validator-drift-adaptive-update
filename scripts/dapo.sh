#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
# The existing development selection must remain fixed across resumes.
test -f data/dapo/manifests/selection_locked.json
scripts/pilot dapo_run generate --limit 20
scripts/pilot dapo_length --pilot
scripts/pilot dapo_run generate
scripts/pilot dapo_length
scripts/pilot dapo_run score
scripts/pilot dapo_evaluate
scripts/pilot dapo_plot
scripts/pilot dapo_audit
