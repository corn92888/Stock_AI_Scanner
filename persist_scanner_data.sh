#!/usr/bin/env bash

set -euo pipefail

commit_message="${1:-chore(data): record scanner signals}"
migration_mode="${CLOUD_EVIDENCE_MODE:-dual_write}"

case "$migration_mode" in
  dual_write|cloud_primary) ;;
  *)
    echo "Invalid CLOUD_EVIDENCE_MODE: $migration_mode" >&2
    exit 1
    ;;
esac

if [ ! -f data/stock_scanner.db ]; then
  if [ "$migration_mode" = "cloud_primary" ]; then
    echo "Cloud-primary persistence requires a restored signal database." >&2
    exit 1
  fi
  echo "No signal database found."
  exit 0
fi

snapshot_dir="$(mktemp -d)"
trap 'rm -rf "$snapshot_dir"' EXIT

mkdir -p "$snapshot_dir/models"
cp data/stock_scanner.db "$snapshot_dir/stock_scanner.db"
if compgen -G 'data/replay_training_samples.csv.gz*' > /dev/null; then
  cp data/replay_training_samples.csv.gz* "$snapshot_dir/"
fi
if compgen -G 'data/replay_execution_labels.csv.gz*' > /dev/null; then
  cp data/replay_execution_labels.csv.gz* "$snapshot_dir/"
fi
if compgen -G 'data/models/*.joblib' > /dev/null; then
  cp data/models/*.joblib "$snapshot_dir/models/"
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Generated files frequently change while code updates land on main. Restore the
# checkout first, then rebuild the snapshot after synchronizing the latest code.
tracked_generated=(data/dashboard_snapshot.json web/public/dashboard_snapshot.json)
if git ls-files --error-unmatch data/stock_scanner.db >/dev/null 2>&1; then
  tracked_generated=(data/stock_scanner.db "${tracked_generated[@]}")
fi
git restore --staged --worktree -- "${tracked_generated[@]}"
git restore --staged --worktree -- data/models 2>/dev/null || true
git restore --staged --worktree -- data/replay_training_samples.csv.gz \
  data/replay_training_samples.csv.gz.metadata.json 2>/dev/null || true
git restore --staged --worktree -- data/replay_execution_labels.csv.gz \
  data/replay_execution_labels.csv.gz.metadata.json 2>/dev/null || true
git pull --rebase origin main

cp "$snapshot_dir/stock_scanner.db" data/stock_scanner.db
if compgen -G "$snapshot_dir/models/*.joblib" > /dev/null; then
  mkdir -p data/models
  cp "$snapshot_dir"/models/*.joblib data/models/
fi
if compgen -G "$snapshot_dir/replay_training_samples.csv.gz*" > /dev/null; then
  cp "$snapshot_dir"/replay_training_samples.csv.gz* data/
fi
if compgen -G "$snapshot_dir/replay_execution_labels.csv.gz*" > /dev/null; then
  cp "$snapshot_dir"/replay_execution_labels.csv.gz* data/
fi

cloud_args=(push --database data/stock_scanner.db)
if [ "${CLOUD_EVIDENCE_ARCHIVE:-false}" = "true" ]; then
  cloud_args+=(--archive-daily)
fi
if [ "$migration_mode" = "cloud_primary" ] || \
   [ "${CLOUD_EVIDENCE_REQUIRED:-false}" = "true" ]; then
  cloud_args+=(--required)
fi
python cloud_evidence.py "${cloud_args[@]}"

if [ "$migration_mode" = "cloud_primary" ] && \
   [ "${CLOUD_EVIDENCE_ARCHIVE:-false}" = "true" ]; then
  python cloud_evidence.py prune \
    --retention-days "${CLOUD_EVIDENCE_RETENTION_DAYS:-45}" \
    --apply \
    --required
fi

python export_dashboard_snapshot.py

git add data/dashboard_snapshot.json web/public/dashboard_snapshot.json
if [ "$migration_mode" = "dual_write" ]; then
  git add data/stock_scanner.db
fi
if compgen -G 'data/replay_training_samples.csv.gz*' > /dev/null; then
  git add data/replay_training_samples.csv.gz*
fi
if compgen -G 'data/replay_execution_labels.csv.gz*' > /dev/null; then
  git add data/replay_execution_labels.csv.gz*
fi
if compgen -G 'data/models/*.joblib' > /dev/null; then
  git add data/models/*.joblib
fi

if git diff --cached --quiet; then
  echo "No scanner data changes to commit."
  exit 0
fi

git commit -m "$commit_message"
git push origin HEAD:main
