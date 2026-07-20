# Cloud Evidence Store and Cutover Control

## Purpose

The operational SQLite database is currently larger than GitHub's recommended
50 MB file size. Committing the complete database after every scheduled job also
creates avoidable merge conflicts and makes recovery depend on Git history.

Cloud Evidence Store adds a private Supabase data plane without changing the
scanner's transaction model:

- SQLite remains the local runtime image used by Python jobs.
- Supabase Storage keeps a verified rolling image and one image per trade date.
- PostgreSQL stores the object manifest, source workflow, run ID, hashes, table
  counts, and append-only synchronization events.
- Git continues to hold the database only during the dual-write validation period.
- A machine-readable restore drill controls whether Cloud Primary may be enabled.
- Daily snapshots older than the configured retention window can be pruned.
- The public dashboard exposes status, timestamps, normalized error codes, and
  the next operator action, but never credentials or raw exception details.

## Initial Setup

1. Open the Supabase SQL Editor and run the current `supabase_schema.sql`.
2. Confirm that the private `scanner-evidence` bucket and all three
   `scanner_evidence_*` tables exist.
3. Keep the existing GitHub Actions secrets:
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
4. Create these Actions repository variables:
   `CLOUD_EVIDENCE_MODE=dual_write`, `CLOUD_EVIDENCE_REQUIRED=false`, and
   `CLOUD_EVIDENCE_RETENTION_DAYS=45`.
5. Manually run `Daily Stock AI Scanner` once.
6. Open the dashboard Operations view and confirm `Cloud Evidence: VERIFIED`.

The service-role key is server-only. It must not be added to a `NEXT_PUBLIC_*`
variable, browser JavaScript, screenshots, logs, or committed configuration.

## Runtime Contract

Every workflow performs these operations in order:

1. Pull the latest code and Git fallback database.
2. Read the verified `live` manifest from Supabase.
3. Restore it only when its latest scan run is newer than the local database.
4. Run the scanner, research, or market update.
5. Create a consistent SQLite backup with the SQLite backup API.
6. Run `PRAGMA integrity_check`, gzip the image, and calculate SHA-256 hashes.
7. Upload the live object and download it again.
8. Verify the compressed and uncompressed hashes before publishing the manifest.
9. Record the result locally and in PostgreSQL, then export the public dashboard.

Daily and historical persistence also overwrite
`daily/YYYY-MM-DD/stock_scanner.db.gz`. Intraday jobs update only the rolling
`live/stock_scanner.db.gz`, so ten scans do not consume ten immutable objects.

## Commands

```bash
python3 cloud_evidence.py push --database data/stock_scanner.db --archive-daily
python3 cloud_evidence.py restore --database data/stock_scanner.db --if-newer
python3 cloud_evidence.py audit --database data/stock_scanner.db --output audit.json
python3 cloud_evidence.py prune --retention-days 45
```

The prune command is a preview unless `--apply` is supplied. Add `--required`
only after the cutover gate below is complete. Without it,
missing credentials or an unavailable first snapshot are recorded as health
warnings while Git remains the authoritative fallback.

## Migration Modes

- `dual_write`: restore from cloud only when newer, publish a verified cloud
  image, and keep committing SQLite to Git as the fallback.
- `cloud_primary`: require a verified restore before every job, require a
  verified push after it, stop staging SQLite in Git, and prune old daily
  objects after archival workflows.

An invalid mode stops the workflow. `CLOUD_EVIDENCE_REQUIRED` remains a legacy
dual-write safety switch; `cloud_primary` is always required even when that
variable is false.

## Cloud-Primary Cutover Gate

Run `Cloud Evidence Cutover Audit` manually. It downloads the live object into
a temporary database, verifies both SHA-256 hashes, runs
`PRAGMA integrity_check`, compares the latest scan run and durable table counts,
and writes the result to local SQLite plus the private PostgreSQL audit ledger.
Vercel also dispatches this audit at 14:10 Asia/Taipei on trading weekdays with
the readiness gate enforced. A blocked result therefore creates a failed audit
run while scanner jobs continue to use the Git fallback in `dual_write` mode.

The gate reports `READY` only when all conditions are true:

- At least two complete trading days show verified live and daily snapshots.
- Every scheduled workflow reports the same latest scan run as the dashboard.
- A restore into a temporary path passes `PRAGMA integrity_check` and row-count
  comparison.
- A daily archive exists for each validation date.
- The live snapshot is no more than 36 hours old.
- The PostgreSQL audit ledger accepts the result.

After a successful audit, first change `CLOUD_EVIDENCE_MODE` to
`cloud_primary` and confirm one scheduled restore/push cycle. Only then remove
the database from tracking with
`git rm --cached data/stock_scanner.db`. Public dashboard JSON and governed
replay datasets remain in Git. Reverting the variable to `dual_write` restores
the Git fallback behavior; recover the last tracked SQLite file before doing so.

Do not bypass a blocked gate. A missing Supabase project, stale URL, schema
error, failed restore, or count mismatch is a production blocker rather than a
warning once Cloud Primary is enabled.

The dashboard maps common failure codes to an explicit repair step:

- `dns_resolution_failed`: confirm the project still exists and update the
  Supabase project URL in GitHub Secrets and local Streamlit secrets.
- `network_timeout`: check Supabase service health and network reachability.
- `http_401`: replace the service-role key.
- `http_403`: verify service-role and Storage bucket permissions.
- `http_404`: reapply `supabase_schema.sql` and recreate the private bucket.

## Recovery

If Supabase is unavailable during dual write, the workflow continues with the
Git database and records a warning. If a downloaded object fails either hash or
SQLite integrity verification, it is never installed. After cloud-primary
cutover, a required restore failure stops the job before any scan is written.
