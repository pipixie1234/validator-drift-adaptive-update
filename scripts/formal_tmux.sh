#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
scripts/pilot formal_pipeline 2>&1 | tee -a runs/formal/controller.log
