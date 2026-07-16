import argparse
import bisect
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from database import INSTITUTIONAL_FEATURE_VERSION
from institutional_flow import institutional_features


DATASET_VERSION = "point_in_time_replay_training_v3_institutional"
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
FLOW_VALUE_COLUMNS = (
    "foreign_net_shares_1d",
    "trust_net_shares_1d",
    "dealer_net_shares_1d",
    "total_net_shares_1d",
    "foreign_net_shares_5d",
    "trust_net_shares_5d",
    "dealer_net_shares_5d",
    "total_net_shares_5d",
    "foreign_net_z20",
    "trust_net_z20",
    "dealer_net_z20",
    "total_net_z20",
    "foreign_buy_ratio_5d",
    "trust_buy_ratio_5d",
    "total_buy_ratio_5d",
    "foreign_streak_days",
    "trust_streak_days",
    "dealer_streak_days",
    "total_streak_days",
    "agreement_score_1d",
)
INSTITUTIONAL_MODEL_FEATURES = (
    "foreign_net_z20",
    "trust_net_z20",
    "dealer_net_z20",
    "total_net_z20",
    "foreign_buy_ratio_5d",
    "trust_buy_ratio_5d",
    "total_buy_ratio_5d",
    "foreign_streak_days",
    "trust_streak_days",
    "total_streak_days",
    "agreement_score_1d",
)
FLOW_QUERY_COLUMNS = (
    "trade_date",
    "code",
    "market",
    "foreign_net_shares",
    "trust_net_shares",
    "dealer_net_shares",
    "total_net_shares",
    "known_at",
    "source_name",
    "payload_sha256",
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value):
    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TAIPEI_TZ)
    return stamp.astimezone(dt.timezone.utc)


def _readonly_connection(path):
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _weekdays(start_date, end_date):
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            yield current.isoformat()
        current += dt.timedelta(days=1)


def _shard_year(path, conn):
    match = re.search(r"(20\d{2})", Path(path).name)
    if match:
        return int(match.group(1))
    row = conn.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM institutional_flow_fetches"
    ).fetchone()
    years = {
        int(str(value)[:4])
        for value in row
        if value and len(str(value)) >= 4
    }
    if len(years) != 1:
        raise ValueError(f"Cannot identify one calendar year for shard: {path}")
    return years.pop()


def inspect_institutional_shard(path, required_start, required_end):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Institutional shard not found: {path}")
    conn = _readonly_connection(path)
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Institutional shard integrity failed: {path}: {integrity}")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required_tables = {"institutional_flow_daily", "institutional_flow_fetches"}
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise ValueError(
                f"Institutional shard {path} is missing tables: "
                + ", ".join(missing_tables)
            )
        year = _shard_year(path, conn)
        start = max(required_start, dt.date(year, 1, 1))
        end = min(required_end, dt.date(year, 12, 31))
        rows = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code), MIN(trade_date), MAX(trade_date) "
            "FROM institutional_flow_daily"
        ).fetchone()
        fetches = conn.execute(
            "SELECT trade_date, market, status FROM institutional_flow_fetches "
            "WHERE trade_date BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall() if start <= end else []
        observed = {(row["trade_date"], row["market"]) for row in fetches}
        expected = {
            (trade_date, market)
            for trade_date in _weekdays(start, end)
            for market in ("上市", "上櫃")
        } if start <= end else set()
        errors = [row for row in fetches if row["status"] == "error"]
        nonterminal = [
            row for row in fetches if row["status"] not in {"available", "no_data"}
        ]
        missing = expected - observed
        return {
            "path": path.name,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "year": year,
            "rows": int(rows[0] or 0),
            "symbols": int(rows[1] or 0),
            "start_date": rows[2],
            "end_date": rows[3],
            "required_start": start.isoformat() if start <= end else None,
            "required_end": end.isoformat() if start <= end else None,
            "fetches": len(fetches),
            "error_fetches": len(errors),
            "nonterminal_fetches": len(nonterminal),
            "missing_required_fetches": len(missing),
            "complete": not nonterminal and not missing,
        }
    finally:
        conn.close()


def _load_code_rows(connections, code, start_date, end_date):
    selected = {}
    columns = ", ".join(FLOW_QUERY_COLUMNS)
    for conn in connections:
        rows = conn.execute(
            f"SELECT {columns} FROM institutional_flow_daily "
            "WHERE code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (code, start_date, end_date),
        ).fetchall()
        for row in rows:
            record = dict(row)
            key = record["trade_date"]
            previous = selected.get(key)
            if previous and (
                previous["market"] != record["market"]
                or previous["payload_sha256"] != record["payload_sha256"]
            ):
                raise ValueError(
                    f"Conflicting institutional rows for {code} on {key}."
                )
            selected[key] = record
    return sorted(selected.values(), key=lambda row: _timestamp(row["known_at"]))


def _feature_record(rows):
    observed = institutional_features(rows)
    result = {column: None for column in FLOW_VALUE_COLUMNS}
    result.update({column: observed.get(column) for column in FLOW_VALUE_COLUMNS})
    result.update(
        {
            "institutional_source_trade_date": observed.get("source_trade_date"),
            "institutional_known_at": observed.get("known_at"),
            "institutional_observations_20d": observed.get("observations_20d", 0),
            "institutional_coverage_status": observed.get("coverage_status", "missing"),
            "institutional_lineage_json": observed.get("lineage_json"),
        }
    )
    return result


def enrich_replay_training_dataset(
    base_dataset,
    shard_paths,
    output_path,
    allow_partial_shards=False,
):
    base_dataset = Path(base_dataset)
    output_path = Path(output_path)
    shard_paths = tuple(Path(path) for path in shard_paths)
    if not base_dataset.exists():
        raise FileNotFoundError(f"Replay training dataset not found: {base_dataset}")
    if not shard_paths:
        raise ValueError("At least one institutional shard is required.")
    if output_path.resolve() == base_dataset.resolve():
        raise ValueError("Institutional output must not overwrite the base dataset.")

    frame = pd.read_csv(base_dataset, dtype={"trade_date": str, "code": str})
    required = {"trade_date", "code", "as_of"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "Replay training dataset missing point-in-time columns: "
            + ", ".join(missing)
        )
    if frame.duplicated(["trade_date", "code"]).any():
        raise ValueError("Replay training dataset contains duplicate event keys.")
    decisions = frame["as_of"].map(_timestamp)
    start_date = dt.date.fromisoformat(str(frame["trade_date"].min())) - dt.timedelta(
        days=60
    )
    end_date = dt.date.fromisoformat(str(frame["trade_date"].max()))
    shard_reports = [
        inspect_institutional_shard(path, start_date, end_date)
        for path in shard_paths
    ]
    incomplete = [report for report in shard_reports if not report["complete"]]
    if incomplete and not allow_partial_shards:
        details = "; ".join(
            f"{report['path']}: {report['error_fetches']} errors, "
            f"{report['nonterminal_fetches']} nonterminal, "
            f"{report['missing_required_fetches']} missing fetches"
            for report in incomplete
        )
        raise ValueError(f"Institutional shards are incomplete: {details}")

    connections = [_readonly_connection(path) for path in shard_paths]
    try:
        records = {}
        for code, group in frame.groupby("code", sort=True):
            rows = _load_code_rows(
                connections, code, start_date.isoformat(), end_date.isoformat()
            )
            known_times = [_timestamp(row["known_at"]) for row in rows]
            for index in group.index:
                decision = decisions.loc[index]
                cutoff = bisect.bisect_right(known_times, decision)
                eligible = list(reversed(rows[max(0, cutoff - 20):cutoff]))
                record = _feature_record(eligible)
                if record["institutional_known_at"] and _timestamp(
                    record["institutional_known_at"]
                ) > decision:
                    raise ValueError(
                        f"Institutional lookahead detected for {code} at {frame.at[index, 'as_of']}."
                    )
                records[index] = record
    finally:
        for connection in connections:
            connection.close()

    features = pd.DataFrame.from_dict(records, orient="index").reindex(frame.index)
    enriched = pd.concat([frame, features], axis=1)
    sort_columns = ["trade_date", "code"]
    if "feature_id" in enriched.columns:
        sort_columns.append("feature_id")
    enriched = enriched.sort_values(sort_columns, kind="stable")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_output,
            mtime=0,
        ) as compressed:
            with io.TextIOWrapper(
                compressed, encoding="utf-8", newline=""
            ) as text_output:
                enriched.to_csv(text_output, index=False, lineterminator="\n")
    coverage_counts = (
        enriched["institutional_coverage_status"]
        .fillna("missing")
        .value_counts()
        .to_dict()
    )
    complete_rows = int(coverage_counts.get("complete", 0))
    metadata = {
        "dataset_version": DATASET_VERSION,
        "institutional_feature_version": INSTITUTIONAL_FEATURE_VERSION,
        "institutional_model_features": list(INSTITUTIONAL_MODEL_FEATURES),
        "availability_policy": "next_calendar_day_0830_asia_taipei",
        "decision_timestamp_column": "as_of",
        "same_day_flow_excluded": True,
        "missing_values_imputed_as_zero": False,
        "samples": int(len(enriched)),
        "complete_samples": complete_rows,
        "complete_coverage_pct": round(
            complete_rows / len(enriched) * 100, 4
        ) if len(enriched) else 0.0,
        "coverage_counts": {key: int(value) for key, value in coverage_counts.items()},
        "start_date": str(enriched["trade_date"].min()),
        "end_date": str(enriched["trade_date"].max()),
        "symbols": int(enriched["code"].nunique()),
        "base_dataset": {
            "path": base_dataset.name,
            "sha256": _sha256(base_dataset),
            "metadata_sha256": (
                _sha256(base_dataset.with_suffix(base_dataset.suffix + ".metadata.json"))
                if base_dataset.with_suffix(base_dataset.suffix + ".metadata.json").exists()
                else None
            ),
        },
        "institutional_shards": shard_reports,
        "partial_shards_allowed": bool(allow_partial_shards),
        "output": output_path.name,
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**metadata, "metadata": str(metadata_path)}


def main():
    parser = argparse.ArgumentParser(
        description="Join official point-in-time institutional flow onto replay samples."
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--shard", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-partial-shards", action="store_true")
    args = parser.parse_args()
    result = enrich_replay_training_dataset(
        args.base,
        args.shard,
        args.output,
        allow_partial_shards=args.allow_partial_shards,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
