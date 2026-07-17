#!/usr/bin/env bash

set -euo pipefail

migration_mode="${CLOUD_EVIDENCE_MODE:-dual_write}"
case "$migration_mode" in
  dual_write)
    args=(restore --database data/stock_scanner.db --if-newer)
    ;;
  cloud_primary)
    args=(restore --database data/stock_scanner.db --required)
    ;;
  *)
    echo "Invalid CLOUD_EVIDENCE_MODE: $migration_mode" >&2
    exit 1
    ;;
esac

if [ "${CLOUD_EVIDENCE_REQUIRED:-false}" = "true" ] && \
   [ "$migration_mode" != "cloud_primary" ]; then
  args+=(--required)
fi

python cloud_evidence.py "${args[@]}"
