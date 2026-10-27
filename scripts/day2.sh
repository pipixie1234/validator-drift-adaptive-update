#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR/src"
mkdir -p runs/day2 reports/day2
run_stage() {
  local stage_name="$1"
  shift
  .venv/bin/python -c 'import json,sys,datetime; from pathlib import Path; Path("runs/day2/state.json").write_text(json.dumps({"status":"running","stage":sys.argv[1],"updated_at":datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()},indent=2))' "$stage_name"
  echo "[$(date -Iseconds)] $stage_name"
  "$@" 2>&1 | tee "runs/day2/${stage_name}.log"
}
run_stage generate_gsm8k scripts/pilot day2_run generate --task gsm8k_C100
run_stage score_gsm8k scripts/pilot day2_run score --task gsm8k_C100
run_stage generate_math scripts/pilot day2_run generate --task math70
run_stage score_math scripts/pilot day2_run score --task math70
run_stage evaluate scripts/pilot day2_evaluate
run_stage audit scripts/pilot day2_audit
sha256sum configs/day2.json src/pilot/day2*.py scripts/day2.sh tests/test_day2_protocol.py > reports/day2/code-sha256.txt
.venv/bin/python -m pip freeze --all > reports/day2/requirements-lock.txt
