#!/usr/bin/env bash

set -euo pipefail

args=(restore --database data/stock_scanner.db --if-newer)
if [ "${CLOUD_EVIDENCE_REQUIRED:-false}" = "true" ]; then
  args+=(--required)
fi

python cloud_evidence.py "${args[@]}"
