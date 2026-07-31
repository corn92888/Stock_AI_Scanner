#!/usr/bin/env bash

set -euo pipefail

migration_mode="${CLOUD_EVIDENCE_MODE:-dual_write}"
database_path="data/stock_scanner.db"
release_tag="${SCANNER_DATA_RELEASE_TAG:-scanner-live-data-v1}"
release_asset="stock_scanner.db.gz"
if [ -x "venv/bin/python" ]; then
  python_bin="venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  python_bin="python3"
fi

restore_release_snapshot() {
  local release_dir
  local repository_args=()
  release_dir="$(mktemp -d)"
  if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    repository_args=(--repo "$GITHUB_REPOSITORY")
  fi

  if ! command -v gh >/dev/null 2>&1; then
    rm -rf "$release_dir"
    return 1
  fi
  if ! gh release download "$release_tag" \
    "${repository_args[@]}" \
    --pattern "$release_asset" \
    --dir "$release_dir" \
    --clobber; then
    rm -rf "$release_dir"
    return 1
  fi

  if ! "$python_bin" - "$release_dir/$release_asset" "$database_path" <<'PY'
import gzip
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    dir=destination.parent,
    prefix=f".{destination.name}.",
    delete=False,
) as temporary:
    temporary_path = Path(temporary.name)
    with gzip.open(archive, "rb") as source:
        while chunk := source.read(1024 * 1024):
            temporary.write(chunk)
try:
    connection = sqlite3.connect(
        f"file:{temporary_path.resolve()}?mode=ro",
        uri=True,
    )
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if integrity != "ok":
        raise RuntimeError(
            f"Release database failed SQLite integrity check: {integrity}"
        )
    os.replace(temporary_path, destination)
finally:
    temporary_path.unlink(missing_ok=True)
PY
  then
    rm -rf "$release_dir"
    return 1
  fi
  rm -rf "$release_dir"
  echo "Restored scanner database from GitHub Release '$release_tag'."
}

case "$migration_mode" in
  dual_write)
    if ! restore_release_snapshot && [ ! -f "$database_path" ]; then
      echo "No durable scanner database could be restored." >&2
      exit 1
    fi
    args=(restore --database "$database_path" --if-newer)
    ;;
  cloud_primary)
    args=(restore --database "$database_path" --required)
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

"$python_bin" cloud_evidence.py "${args[@]}"
