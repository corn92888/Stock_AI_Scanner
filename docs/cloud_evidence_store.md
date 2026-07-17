# Cloud Evidence Store v1

## Purpose

The operational SQLite database is currently larger than GitHub's recommended
50 MB file size. Committing the complete database after every scheduled job also
creates avoidable merge conflicts and makes recovery depend on Git history.

Cloud Evidence Store v1 adds a private Supabase data plane without changing the
scanner's transaction model:

- SQLite remains the local runtime image used by Python jobs.
- Supabase Storage keeps a verified rolling image and one image per trade date.
- PostgreSQL stores the object manifest, source workflow, run ID, hashes, table
  counts, and append-only synchronization events.
- Git continues to hold the database during the dual-write validation period.
- The public dashboard exposes status and timestamps, but never credentials or
  raw synchronization errors.

## Initial Setup

1. Open the Supabase SQL Editor and run the current `supabase_schema.sql`.
2. Confirm that the private `scanner-evidence` bucket and both
   `scanner_evidence_*` tables exist.
3. Keep the existing GitHub Actions secrets:
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
4. Create the Actions repository variable `CLOUD_EVIDENCE_REQUIRED=false`.
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
```

Add `--required` only after the cutover gate below is complete. Without it,
missing credentials or an unavailable first snapshot are recorded as health
warnings while Git remains the authoritative fallback.

## Cloud-Primary Cutover Gate

Do not remove `data/stock_scanner.db` from Git until all conditions are true:

- At least two complete trading days show verified live and daily snapshots.
- Every scheduled workflow reports the same latest scan run as the dashboard.
- A restore into a temporary path passes `PRAGMA integrity_check` and row-count
  comparison.
- A daily archive exists for each validation date.
- The Operations view has no failed or stale cloud evidence warning.

The next migration changes `CLOUD_EVIDENCE_REQUIRED` to `true`, removes the
database from Git tracking, and keeps public JSON plus governed replay datasets
in their existing locations. This switch is intentionally separate from v1 so
the production scheduler is never used as an untested one-way migration.

## Recovery

If Supabase is unavailable during dual write, the workflow continues with the
Git database and records a warning. If a downloaded object fails either hash or
SQLite integrity verification, it is never installed. After cloud-primary
cutover, a required restore failure stops the job before any scan is written.
