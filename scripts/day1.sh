#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR/src"
run_stage() {
  local stage_name="$1"
  shift
  echo "[$(date -Iseconds)] $stage_name"
  "$@" 2>&1 | tee "runs/day1/${stage_name}.log"
}
run_stage inspect scripts/pilot inspect
.venv/bin/python -m pip check | tee reports/pip-check.txt
run_stage tests .venv/bin/python -m pytest tests -q
run_stage generate_C scripts/pilot generate --split C --limit 50 --tag G0_C50
run_stage generate_A_smoke scripts/pilot generate --split A --limit 4 --tag G0_A_smoke
run_stage generate_B_smoke scripts/pilot generate --split B --limit 24 --tag G0_B_smoke
run_stage score_C scripts/pilot score
if [ ! -f runs/day1/smoke_G_report.json ]; then run_stage train_G_smoke scripts/pilot train_smoke --role G; fi
if [ ! -f runs/day1/smoke_V_report.json ]; then run_stage train_V_smoke scripts/pilot train_smoke --role V; fi
run_stage report scripts/pilot report
run_stage audit scripts/pilot audit
.venv/bin/python -m pip freeze --all > requirements-lock.txt
sha256sum configs/day1.json src/pilot/*.py scripts/* tests/*.py > reports/code-sha256.txt
