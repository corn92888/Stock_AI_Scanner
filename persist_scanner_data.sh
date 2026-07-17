#!/usr/bin/env bash

set -euo pipefail

commit_message="${1:-chore(data): record scanner signals}"

if [ ! -f data/stock_scanner.db ]; then
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
git restore --staged --worktree -- \
  data/stock_scanner.db \
  data/dashboard_snapshot.json \
  web/public/dashboard_snapshot.json
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
if [ "${CLOUD_EVIDENCE_REQUIRED:-false}" = "true" ]; then
  cloud_args+=(--required)
fi
python cloud_evidence.py "${cloud_args[@]}"

python export_dashboard_snapshot.py

git add data/stock_scanner.db data/dashboard_snapshot.json web/public/dashboard_snapshot.json
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
