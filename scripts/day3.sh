#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
# Original development generation and V scoring must already contain400 records.
# The supplementary replay stays separate; successful smoke checkpoints are reused.
scripts/pilot day3_length_replay
scripts/pilot day3_protocol prepare
scripts/pilot day3_protocol freeze
scripts/pilot day3_smoke --role G
scripts/pilot day3_smoke --role V
scripts/pilot day3_finish
