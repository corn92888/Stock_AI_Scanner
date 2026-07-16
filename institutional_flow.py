import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database import (
    DB_PATH,
    INSTITUTIONAL_FEATURE_VERSION,
    get_connection,
    get_taipei_now,
    init_db,
)


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
TWSE_SOURCE = "TWSE T86"
TPEX_SOURCE = "TPEx institutional daily trade"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)
DEFAULT_LOOKBACK_CALENDAR_DAYS = 45
DEFAULT_RETENTION_DAYS = 60


class InstitutionalDataError(ValueError):
    pass


@dataclass(frozen=True)
class MarketReport:
    market: str
    trade_date: str
    source_name: str
    source_url: str
    payload_sha256: str
    fetched_at: str
    records: tuple


def _number(value):
    if value is None:
        return 0
    text = str(value).strip().replace(",", "").replace("+", "")
    if not text or text in {"--", "---"}:
        return 0
    try:
        return int(float(text))
    except ValueError as exc:
        raise InstitutionalDataError(f"Invalid institutional share value: {value}") from exc


def _stock_code(value):
    code = str(value or "").strip()
    return code if len(code) == 4 and code.isdigit() else ""


def _iso_date(value):
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return dt.date.fromisoformat(str(value)).isoformat()


def _roc_date(value):
    date = dt.date.fromisoformat(_iso_date(value))
    return f"{date.year - 1911:03d}/{date.month:02d}/{date.day:02d}"


def _parse_roc_date(value):
    year, month, day = (int(part) for part in str(value).split("/"))
    return dt.date(year + 1911, month, day).isoformat()


def _known_at(trade_date):
    date = dt.date.fromisoformat(_iso_date(trade_date)) + dt.timedelta(days=1)
    return dt.datetime.combine(date, dt.time(8, 30), tzinfo=TAIPEI_TZ).isoformat()


def _record(
    *,
    trade_date,
    code,
    name,
    market,
    foreign_buy,
    foreign_sell,
    foreign_net,
    trust_buy,
    trust_sell,
    trust_net,
    dealer_buy,
    dealer_sell,
    dealer_net,
    total_net,
    source_name,
    source_url,
    payload_sha256,
    fetched_at,
):
    return {
        "trade_date": trade_date,
        "code": code,
        "name": str(name or "").strip(),
        "market": market,
        "foreign_buy_shares": _number(foreign_buy),
        "foreign_sell_shares": _number(foreign_sell),
        "foreign_net_shares": _number(foreign_net),
        "trust_buy_shares": _number(trust_buy),
        "trust_sell_shares": _number(trust_sell),
        "trust_net_shares": _number(trust_net),
        "dealer_buy_shares": _number(dealer_buy),
        "dealer_sell_shares": _number(dealer_sell),
        "dealer_net_shares": _number(dealer_net),
        "total_net_shares": _number(total_net),
        "known_at": _known_at(trade_date),
        "source_name": source_name,
        "source_url": source_url,
        "payload_sha256": payload_sha256,
        "fetched_at": fetched_at,
    }


def parse_twse_payload(payload, requested_date, source_url, payload_sha256, fetched_at):
    requested_date = _iso_date(requested_date)
    if payload.get("stat") != "OK":
        return MarketReport(
            "上市", requested_date, TWSE_SOURCE, source_url, payload_sha256, fetched_at, ()
        )
    report_date = dt.datetime.strptime(str(payload.get("date")), "%Y%m%d").date().isoformat()
    if report_date != requested_date:
        raise InstitutionalDataError(
            f"TWSE report date {report_date} does not match {requested_date}."
        )
    rows = payload.get("data") or []
    if rows and len(rows[0]) < 19:
        raise InstitutionalDataError("TWSE T86 schema has fewer than 19 columns.")
    records = []
    for row in rows:
        code = _stock_code(row[0])
        if not code:
            continue
        records.append(
            _record(
                trade_date=report_date,
                code=code,
                name=row[1],
                market="上市",
                foreign_buy=row[2],
                foreign_sell=row[3],
                foreign_net=row[4],
                trust_buy=row[8],
                trust_sell=row[9],
                trust_net=row[10],
                dealer_buy=_number(row[12]) + _number(row[15]),
                dealer_sell=_number(row[13]) + _number(row[16]),
                dealer_net=row[11],
                total_net=row[18],
                source_name=TWSE_SOURCE,
                source_url=source_url,
                payload_sha256=payload_sha256,
                fetched_at=fetched_at,
            )
        )
    return MarketReport(
        "上市",
        report_date,
        TWSE_SOURCE,
        source_url,
        payload_sha256,
        fetched_at,
        tuple(records),
    )


def parse_tpex_payload(payload, requested_date, source_url, payload_sha256, fetched_at):
    requested_date = _iso_date(requested_date)
    tables = payload.get("tables") or []
    if not tables:
        return MarketReport(
            "上櫃", requested_date, TPEX_SOURCE, source_url, payload_sha256, fetched_at, ()
        )
    table = tables[0]
    report_date = _parse_roc_date(table.get("date"))
    if report_date != requested_date:
        raise InstitutionalDataError(
            f"TPEx report date {report_date} does not match {requested_date}."
        )
    rows = table.get("data") or []
    if rows and len(rows[0]) < 24:
        raise InstitutionalDataError("TPEx institutional schema has fewer than 24 columns.")
    records = []
    for row in rows:
        code = _stock_code(row[0])
        if not code:
            continue
        records.append(
            _record(
                trade_date=report_date,
                code=code,
                name=row[1],
                market="上櫃",
                foreign_buy=row[2],
                foreign_sell=row[3],
                foreign_net=row[4],
                trust_buy=row[11],
                trust_sell=row[12],
                trust_net=row[13],
                dealer_buy=row[20],
                dealer_sell=row[21],
                dealer_net=row[22],
                total_net=row[23],
                source_name=TPEX_SOURCE,
                source_url=source_url,
                payload_sha256=payload_sha256,
                fetched_at=fetched_at,
            )
        )
    return MarketReport(
        "上櫃",
        report_date,
        TPEX_SOURCE,
        source_url,
        payload_sha256,
        fetched_at,
        tuple(records),
    )


def _session():
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504, 520),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Stock-AI-Scanner/1.0 institutional-research",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_market_report(session, market, trade_date, timeout=30):
    trade_date = _iso_date(trade_date)
    if market == "上市":
        response = session.get(
            TWSE_URL,
            params={
                "date": trade_date.replace("-", ""),
                "selectType": "ALLBUT0999",
                "response": "json",
            },
            timeout=timeout,
        )
        parser = parse_twse_payload
    elif market == "上櫃":
        response = session.get(
            TPEX_URL,
            params={
                "l": "zh-tw",
                "o": "json",
                "se": "EW",
                "t": "D",
                "d": _roc_date(trade_date),
            },
            timeout=timeout,
        )
        parser = parse_tpex_payload
    else:
        raise ValueError(f"Unsupported market: {market}")
    response.raise_for_status()
    payload_sha256 = hashlib.sha256(response.content).hexdigest()
    fetched_at = get_taipei_now().isoformat(timespec="seconds")
    return parser(
        response.json(), trade_date, response.url, payload_sha256, fetched_at
    )


def _save_report(conn, report):
    flow_columns = [
        "trade_date",
        "code",
        "name",
        "market",
        "foreign_buy_shares",
        "foreign_sell_shares",
        "foreign_net_shares",
        "trust_buy_shares",
        "trust_sell_shares",
        "trust_net_shares",
        "dealer_buy_shares",
        "dealer_sell_shares",
        "dealer_net_shares",
        "total_net_shares",
        "known_at",
        "source_name",
        "source_url",
        "payload_sha256",
        "fetched_at",
    ]
    for record in report.records:
        conn.execute(
            f"""
            INSERT INTO institutional_flow_daily ({', '.join(flow_columns)})
            VALUES ({', '.join('?' for _ in flow_columns)})
            ON CONFLICT(trade_date, code, market) DO UPDATE SET
                name=excluded.name,
                foreign_buy_shares=excluded.foreign_buy_shares,
                foreign_sell_shares=excluded.foreign_sell_shares,
                foreign_net_shares=excluded.foreign_net_shares,
                trust_buy_shares=excluded.trust_buy_shares,
                trust_sell_shares=excluded.trust_sell_shares,
                trust_net_shares=excluded.trust_net_shares,
                dealer_buy_shares=excluded.dealer_buy_shares,
                dealer_sell_shares=excluded.dealer_sell_shares,
                dealer_net_shares=excluded.dealer_net_shares,
                total_net_shares=excluded.total_net_shares,
                known_at=excluded.known_at,
                source_name=excluded.source_name,
                source_url=excluded.source_url,
                payload_sha256=excluded.payload_sha256,
                fetched_at=excluded.fetched_at
            """,
            tuple(record[column] for column in flow_columns),
        )
    conn.execute(
        """
        INSERT INTO institutional_flow_fetches (
            trade_date, market, status, report_date, row_count, source_name,
            source_url, payload_sha256, fetched_at, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(trade_date, market) DO UPDATE SET
            status=excluded.status, report_date=excluded.report_date,
            row_count=excluded.row_count, source_name=excluded.source_name,
            source_url=excluded.source_url,
            payload_sha256=excluded.payload_sha256,
            fetched_at=excluded.fetched_at, error_text=NULL
        """,
        (
            report.trade_date,
            report.market,
            "available" if report.records else "no_data",
            report.trade_date,
            len(report.records),
            report.source_name,
            report.source_url,
            report.payload_sha256,
            report.fetched_at,
        ),
    )


def _save_fetch_error(conn, trade_date, market, source_name, source_url, error):
    conn.execute(
        """
        INSERT INTO institutional_flow_fetches (
            trade_date, market, status, row_count, source_name, source_url,
            fetched_at, error_text
        ) VALUES (?, ?, 'error', 0, ?, ?, ?, ?)
        ON CONFLICT(trade_date, market) DO UPDATE SET
            status='error', row_count=0, source_name=excluded.source_name,
            source_url=excluded.source_url, fetched_at=excluded.fetched_at,
            error_text=excluded.error_text
        """,
        (
            trade_date,
            market,
            source_name,
            source_url,
            get_taipei_now().isoformat(timespec="seconds"),
            str(error)[:1000],
        ),
    )


def collect_institutional_flows(
    start_date,
    end_date,
    db_path=DB_PATH,
    refresh=False,
    retention_days=DEFAULT_RETENTION_DAYS,
    sleep_seconds=0.15,
    session=None,
):
    start = dt.date.fromisoformat(_iso_date(start_date))
    end = dt.date.fromisoformat(_iso_date(end_date))
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    session = session or _session()
    result = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "requests": 0,
        "skipped": 0,
        "available_reports": 0,
        "no_data_reports": 0,
        "error_reports": 0,
        "rows_saved": 0,
        "errors": [],
    }
    with get_connection(db_path) as conn:
        init_db(conn)
        current = start
        while current <= end:
            if current.weekday() < 5:
                for market, source_name, source_url in (
                    ("上市", TWSE_SOURCE, TWSE_URL),
                    ("上櫃", TPEX_SOURCE, TPEX_URL),
                ):
                    previous = conn.execute(
                        "SELECT status FROM institutional_flow_fetches "
                        "WHERE trade_date=? AND market=?",
                        (current.isoformat(), market),
                    ).fetchone()
                    terminal = previous and previous["status"] in {
                        "available",
                        "no_data",
                    }
                    if terminal and not refresh and current < end:
                        result["skipped"] += 1
                        continue
                    try:
                        report = fetch_market_report(
                            session, market, current.isoformat()
                        )
                        _save_report(conn, report)
                        result["requests"] += 1
                        result["rows_saved"] += len(report.records)
                        key = "available_reports" if report.records else "no_data_reports"
                        result[key] += 1
                    except Exception as exc:
                        _save_fetch_error(
                            conn,
                            current.isoformat(),
                            market,
                            source_name,
                            source_url,
                            exc,
                        )
                        result["requests"] += 1
                        result["error_reports"] += 1
                        result["errors"].append(
                            f"{current.isoformat()} {market}: {exc}"
                        )
                    conn.commit()
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
            current += dt.timedelta(days=1)

        if retention_days and retention_days > 0:
            cutoff = end - dt.timedelta(days=retention_days)
            result["rows_pruned"] = conn.execute(
                "DELETE FROM institutional_flow_daily WHERE trade_date < ?",
                (cutoff.isoformat(),),
            ).rowcount
        else:
            result["rows_pruned"] = 0
    return result


def _timestamp(value):
    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TAIPEI_TZ)
    return stamp.astimezone(dt.timezone.utc)


def _zscore(values):
    if not values:
        return None
    deviation = statistics.pstdev(values)
    if not math.isfinite(deviation) or deviation == 0:
        return 0.0
    return (values[0] - statistics.mean(values)) / deviation


def _signed_streak(values):
    if not values or values[0] == 0:
        return 0
    sign = 1 if values[0] > 0 else -1
    count = 0
    for value in values:
        if value == 0 or (1 if value > 0 else -1) != sign:
            break
        count += 1
    return sign * count


def _ratio_positive(values):
    return sum(value > 0 for value in values) / len(values) if values else None


def institutional_features(rows):
    rows = list(rows)[:20]
    if not rows:
        return {
            "source_trade_date": None,
            "known_at": None,
            "observations_20d": 0,
            "coverage_status": "missing",
            "lineage_json": json.dumps(
                {"reason": "no_point_in_time_institutional_rows"}, sort_keys=True
            ),
        }
    fields = {
        "foreign": [int(row["foreign_net_shares"]) for row in rows],
        "trust": [int(row["trust_net_shares"]) for row in rows],
        "dealer": [int(row["dealer_net_shares"]) for row in rows],
        "total": [int(row["total_net_shares"]) for row in rows],
    }
    recent = {key: values[:5] for key, values in fields.items()}
    latest = rows[0]
    count = len(rows)
    result = {
        "source_trade_date": latest["trade_date"],
        "known_at": latest["known_at"],
        "observations_20d": count,
        "coverage_status": "complete" if count >= 20 else "partial" if count >= 5 else "insufficient",
        "agreement_score_1d": sum(
            1 if fields[key][0] > 0 else -1 if fields[key][0] < 0 else 0
            for key in ("foreign", "trust", "dealer")
        ),
    }
    for key, values in fields.items():
        result[f"{key}_net_shares_1d"] = values[0]
        result[f"{key}_net_shares_5d"] = sum(recent[key])
        result[f"{key}_net_z20"] = _zscore(values)
        result[f"{key}_streak_days"] = _signed_streak(values)
    for key in ("foreign", "trust", "total"):
        result[f"{key}_buy_ratio_5d"] = _ratio_positive(recent[key])
    result["lineage_json"] = json.dumps(
        {
            "availability_policy": "next_calendar_day_0830_asia_taipei",
            "feature_version": INSTITUTIONAL_FEATURE_VERSION,
            "latest_payload_sha256": latest["payload_sha256"],
            "latest_source": latest["source_name"],
            "source_trade_date": latest["trade_date"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return result


def build_institutional_feature_snapshots(
    db_path=DB_PATH, scope="latest-trade-date", rebuild=False
):
    where = []
    params = [INSTITUTIONAL_FEATURE_VERSION]
    if not rebuild:
        where.append("ifs.id IS NULL")
    if scope == "latest-trade-date":
        where.append("sr.trade_date=(SELECT MAX(trade_date) FROM scan_runs)")
    elif scope == "latest-run":
        where.append("sr.id=(SELECT MAX(id) FROM scan_runs)")
    elif scope != "all-unfeatured":
        raise ValueError(f"Unsupported feature scope: {scope}")
    where_sql = " AND ".join(where) if where else "1=1"
    columns = [
        "run_id",
        "code",
        "decision_at",
        "source_trade_date",
        "known_at",
        "feature_version",
        "observations_20d",
        "coverage_status",
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
        "total_streak_days",
        "agreement_score_1d",
        "lineage_json",
        "created_at",
    ]
    built = 0
    coverage = {"complete": 0, "partial": 0, "insufficient": 0, "missing": 0}
    with get_connection(db_path) as conn:
        init_db(conn)
        events = conn.execute(
            f"""
            SELECT ce.run_id, ce.code, COALESCE(ce.as_of, sr.run_at) AS decision_at
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            LEFT JOIN institutional_feature_snapshots ifs
              ON ifs.run_id=ce.run_id AND ifs.code=ce.code
             AND ifs.feature_version=?
            WHERE {where_sql}
            ORDER BY ce.run_id, ce.code
            """,
            tuple(params),
        ).fetchall()
        now = get_taipei_now().isoformat(timespec="seconds")
        for event in events:
            decision = _timestamp(event["decision_at"])
            raw_rows = conn.execute(
                """
                SELECT * FROM institutional_flow_daily
                WHERE code=?
                ORDER BY trade_date DESC, id DESC
                LIMIT 40
                """,
                (event["code"],),
            ).fetchall()
            eligible = [
                row for row in raw_rows if _timestamp(row["known_at"]) <= decision
            ][:20]
            features = institutional_features(eligible)
            record = {
                "run_id": event["run_id"],
                "code": event["code"],
                "decision_at": event["decision_at"],
                "feature_version": INSTITUTIONAL_FEATURE_VERSION,
                "created_at": now,
                **features,
            }
            conn.execute(
                f"""
                INSERT INTO institutional_feature_snapshots ({', '.join(columns)})
                VALUES ({', '.join('?' for _ in columns)})
                ON CONFLICT(run_id, code, feature_version) DO UPDATE SET
                    {', '.join(f'{column}=excluded.{column}' for column in columns if column not in {'run_id', 'code', 'feature_version', 'created_at'})}
                """,
                tuple(record.get(column) for column in columns),
            )
            built += 1
            coverage[features["coverage_status"]] += 1
    return {"built": built, "coverage": coverage, "feature_version": INSTITUTIONAL_FEATURE_VERSION}


def _as_of_status(db_path, as_of):
    with get_connection(db_path) as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT market, status, row_count, fetched_at, error_text "
            "FROM institutional_flow_fetches WHERE trade_date=? ORDER BY market",
            (_iso_date(as_of),),
        ).fetchall()
    return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser(
        description="Collect point-in-time institutional trading flow from TWSE and TPEx."
    )
    parser.add_argument("--database", default=str(DB_PATH))
    parser.add_argument("--as-of")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument(
        "--lookback-calendar-days", type=int, default=DEFAULT_LOOKBACK_CALENDAR_DAYS
    )
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--require-as-of", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument(
        "--feature-scope",
        choices=("latest-trade-date", "latest-run", "all-unfeatured"),
        default="latest-trade-date",
    )
    parser.add_argument("--no-build-features", action="store_true")
    args = parser.parse_args()

    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be provided together")
    if args.start and args.as_of:
        parser.error("use either --as-of or --start/--end")
    if args.start:
        start = dt.date.fromisoformat(args.start)
        as_of = dt.date.fromisoformat(args.end)
    else:
        as_of = (
            dt.date.fromisoformat(args.as_of) if args.as_of else get_taipei_now().date()
        )
        start = as_of - dt.timedelta(days=max(1, args.lookback_calendar_days) - 1)
    collection = collect_institutional_flows(
        start,
        as_of,
        db_path=Path(args.database),
        refresh=args.refresh,
        retention_days=args.retention_days,
    )
    features = (
        None
        if args.no_build_features
        else build_institutional_feature_snapshots(
            db_path=Path(args.database), scope=args.feature_scope
        )
    )
    statuses = _as_of_status(Path(args.database), as_of)
    result = {
        "as_of": as_of.isoformat(),
        "collection": collection,
        "features": features,
        "as_of_sources": statuses,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_error and collection["error_reports"]:
        raise SystemExit(
            f"Institutional collection had {collection['error_reports']} source errors."
        )
    if args.require_as_of:
        available = {row["market"] for row in statuses if row["status"] == "available"}
        if available != {"上市", "上櫃"}:
            raise SystemExit(
                f"Official institutional reports incomplete for {as_of}: {sorted(available)}"
            )


if __name__ == "__main__":
    main()
