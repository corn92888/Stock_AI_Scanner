import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import ssl
import time
import urllib.request
from dataclasses import dataclass

from database import (
    DB_PATH,
    FUNDAMENTAL_SOURCE_VERSION,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
TWSE_BASE_URL = "https://openapi.twse.com.tw/v1"
TPEX_BASE_URL = "https://www.tpex.org.tw/openapi/v1"

SOURCE_ENDPOINTS = {
    "twse_valuation": f"{TWSE_BASE_URL}/exchangeReport/BWIBBU_d",
    "twse_revenue": f"{TWSE_BASE_URL}/opendata/t187ap05_L",
    "twse_eps": f"{TWSE_BASE_URL}/opendata/t187ap14_L",
    "tpex_valuation": f"{TPEX_BASE_URL}/tpex_mainboard_peratio_analysis",
    "tpex_revenue": f"{TPEX_BASE_URL}/mopsfin_t187ap05_O",
    "tpex_eps": f"{TPEX_BASE_URL}/mopsfin_t187ap14_O",
}


class FundamentalDataError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePayload:
    key: str
    url: str
    records: tuple
    sha256: str


def _ssl_context():
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        context.verify_flags &= ~strict_flag
    return context


class OfficialFundamentalProvider:
    def __init__(self, timeout=30, retries=3):
        self.timeout = int(timeout)
        self.retries = int(retries)

    def fetch_one(self, key, url):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Stock-AI-Scanner/1.0 official-data-research",
            },
        )
        error = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                    context=_ssl_context(),
                ) as response:
                    raw = response.read()
                records = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(records, list):
                    raise FundamentalDataError(f"{key} did not return a JSON list")
                return SourcePayload(
                    key=key,
                    url=url,
                    records=tuple(records),
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            except Exception as exc:
                error = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
        raise FundamentalDataError(f"Unable to fetch {key}: {error}") from error

    def fetch_all(self):
        payloads = {}
        errors = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self.fetch_one, key, url): key
                for key, url in SOURCE_ENDPOINTS.items()
            }
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    payloads[key] = future.result()
                except Exception as exc:
                    errors[key] = str(exc)
        return payloads, errors


def _number(value):
    text = str(value or "").strip().replace(",", "").replace("+", "")
    if not text or text in {"-", "--", "---", "N/A", "nan"}:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def _stock_code(value):
    code = str(value or "").strip()
    return code if len(code) == 4 and code.isdigit() else ""


def _gregorian_date(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) == 8 and int(digits[:4]) >= 1911:
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    if len(digits) == 7:
        return dt.date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7]))
    raise FundamentalDataError(f"Invalid official report date: {value}")


def _roc_period(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 5:
        raise FundamentalDataError(f"Invalid ROC year-month: {value}")
    return f"{int(digits[:3]) + 1911:04d}-{int(digits[3:5]):02d}"


def _eps_period(year, quarter):
    year_text = "".join(character for character in str(year or "") if character.isdigit())
    quarter_number = int(str(quarter or "0").strip())
    if len(year_text) not in {3, 4} or quarter_number not in {1, 2, 3, 4}:
        raise FundamentalDataError(f"Invalid EPS period: {year}/{quarter}")
    year_number = int(year_text) + (1911 if len(year_text) == 3 else 0)
    return f"{year_number:04d}Q{quarter_number}"


def _published_at(report_date):
    return dt.datetime.combine(
        report_date,
        dt.time(18, 0),
        tzinfo=TAIPEI_TZ,
    ).isoformat()


def _source_record_base(record, market, report_date, raw):
    return {
        "market": market,
        "report_date": report_date.isoformat(),
        "raw": raw,
    }


def _parse_valuation(payload, market, now_date):
    parsed = {}
    for raw in payload.records:
        code = _stock_code(
            raw.get("Code") if market == "TWSE" else raw.get("SecuritiesCompanyCode")
        )
        if not code:
            continue
        try:
            report_date = _gregorian_date(raw.get("Date"))
        except FundamentalDataError:
            continue
        if report_date > now_date:
            continue
        parsed[code] = {
            **_source_record_base(raw, market, report_date, raw),
            "name": str(raw.get("Name") or raw.get("CompanyName") or "").strip(),
            "pe": _number(raw.get("PEratio") or raw.get("PriceEarningRatio")),
            "pb": _number(raw.get("PBratio") or raw.get("PriceBookRatio")),
            "valuation_date": report_date.isoformat(),
        }
    return parsed


def _parse_revenue(payload, market, now_date):
    parsed = {}
    for raw in payload.records:
        code = _stock_code(raw.get("公司代號"))
        if not code:
            continue
        try:
            report_date = _gregorian_date(raw.get("出表日期"))
            period = _roc_period(raw.get("資料年月"))
        except FundamentalDataError:
            continue
        if report_date > now_date:
            continue
        parsed[code] = {
            **_source_record_base(raw, market, report_date, raw),
            "name": str(raw.get("公司名稱") or "").strip(),
            "revenue_yoy": _number(raw.get("營業收入-去年同月增減(%)")),
            "revenue_mom": _number(raw.get("營業收入-上月比較增減(%)")),
            "revenue_period": period,
        }
    return parsed


def _parse_eps(payload, market, now_date):
    parsed = {}
    for raw in payload.records:
        code = _stock_code(raw.get("公司代號"))
        if not code:
            continue
        try:
            report_date = _gregorian_date(raw.get("出表日期"))
            period = _eps_period(raw.get("年度"), raw.get("季別"))
        except (FundamentalDataError, ValueError):
            continue
        if report_date > now_date:
            continue
        parsed[code] = {
            **_source_record_base(raw, market, report_date, raw),
            "name": str(raw.get("公司名稱") or "").strip(),
            "eps_latest": _number(raw.get("基本每股盈餘(元)")),
            "eps_period": period,
        }
    return parsed


def normalize_payloads(payloads, now=None):
    now = now or get_taipei_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    now = now.astimezone(TAIPEI_TZ)
    parsed = {}
    parsers = (
        ("twse_valuation", "TWSE", _parse_valuation, "valuation"),
        ("twse_revenue", "TWSE", _parse_revenue, "revenue"),
        ("twse_eps", "TWSE", _parse_eps, "eps"),
        ("tpex_valuation", "TPEx", _parse_valuation, "valuation"),
        ("tpex_revenue", "TPEx", _parse_revenue, "revenue"),
        ("tpex_eps", "TPEx", _parse_eps, "eps"),
    )
    for key, market, parser, component in parsers:
        payload = payloads.get(key)
        if not payload:
            continue
        for code, row in parser(payload, market, now.date()).items():
            target = parsed.setdefault(
                code,
                {"code": code, "market": market, "components": {}},
            )
            target["components"][component] = row
            target["name"] = target.get("name") or row.get("name")

    observations = []
    for code, row in sorted(parsed.items()):
        components = row["components"]
        valuation = components.get("valuation", {})
        revenue = components.get("revenue", {})
        eps = components.get("eps", {})
        dates = [
            component.get("report_date")
            for component in components.values()
            if component.get("report_date")
        ]
        quality_flags = [
            f"missing_{component}"
            for component in ("valuation", "revenue", "eps")
            if component not in components
        ]
        observations.append(
            {
                "code": code,
                "name": row.get("name") or "",
                "market": row["market"],
                "period_end": max(dates) if dates else now.date().isoformat(),
                "published_at": _published_at(dt.date.fromisoformat(max(dates))),
                "known_at": now.isoformat(timespec="seconds"),
                "source_name": FUNDAMENTAL_SOURCE_VERSION,
                "source_url": TWSE_BASE_URL if row["market"] == "TWSE" else TPEX_BASE_URL,
                "valuation_date": valuation.get("valuation_date"),
                "revenue_period": revenue.get("revenue_period"),
                "eps_period": eps.get("eps_period"),
                "pe": valuation.get("pe"),
                "pb": valuation.get("pb"),
                "revenue_yoy": revenue.get("revenue_yoy"),
                "revenue_mom": revenue.get("revenue_mom"),
                "eps_ttm": None,
                "eps_latest": eps.get("eps_latest"),
                "quality_flags": quality_flags,
                "components": components,
            }
        )
    return observations


def _payload_manifest(payloads):
    return {
        key: {
            "url": payload.url,
            "sha256": payload.sha256,
            "records": len(payload.records),
        }
        for key, payload in sorted(payloads.items())
    }


def persist_observations(conn, run_id, observations, created_at):
    persisted = 0
    for row in observations:
        raw_json = json.dumps(
            {
                "name": row["name"],
                "market": row["market"],
                "components": row["components"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_sha256 = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO fundamental_observations (
                code, period_end, published_at, known_at, source_name,
                pe, pb, revenue_yoy, revenue_mom, eps_ttm, raw_json,
                created_at, market, valuation_date, revenue_period,
                eps_period, eps_latest, source_url, payload_sha256,
                quality_flags_json, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, source_name, period_end, published_at) DO UPDATE SET
                pe=excluded.pe,
                pb=excluded.pb,
                revenue_yoy=excluded.revenue_yoy,
                revenue_mom=excluded.revenue_mom,
                eps_latest=excluded.eps_latest,
                raw_json=excluded.raw_json,
                market=excluded.market,
                valuation_date=excluded.valuation_date,
                revenue_period=excluded.revenue_period,
                eps_period=excluded.eps_period,
                source_url=excluded.source_url,
                payload_sha256=excluded.payload_sha256,
                quality_flags_json=excluded.quality_flags_json,
                known_at=MIN(fundamental_observations.known_at, excluded.known_at)
            """,
            (
                row["code"],
                row["period_end"],
                row["published_at"],
                row["known_at"],
                row["source_name"],
                row["pe"],
                row["pb"],
                row["revenue_yoy"],
                row["revenue_mom"],
                row["eps_ttm"],
                raw_json,
                created_at,
                row["market"],
                row["valuation_date"],
                row["revenue_period"],
                row["eps_period"],
                row["eps_latest"],
                row["source_url"],
                payload_sha256,
                json.dumps(row["quality_flags"], ensure_ascii=False),
                run_id,
            ),
        )
        persisted += 1
    return persisted


def ingest_official_fundamentals(db_path=DB_PATH, provider=None, now=None):
    now = now or get_taipei_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    now = now.astimezone(TAIPEI_TZ)
    started_at = now.isoformat(timespec="seconds")
    snapshot_date = now.date().isoformat()
    provider = provider or OfficialFundamentalProvider()
    payloads, errors = provider.fetch_all()
    observations = normalize_payloads(payloads, now=now)
    warnings = [f"{key}: {message}" for key, message in sorted(errors.items())]
    status = "completed" if not errors else "partial"
    if not observations:
        status = "failed"
        warnings.append("No valid official fundamental observations were returned.")

    twse_count = sum(1 for row in observations if row["market"] == "TWSE")
    tpex_count = sum(1 for row in observations if row["market"] == "TPEx")
    valuation_dates = sorted(
        {row["valuation_date"] for row in observations if row["valuation_date"]}
    )
    revenue_periods = sorted(
        {row["revenue_period"] for row in observations if row["revenue_period"]}
    )
    eps_periods = sorted({row["eps_period"] for row in observations if row["eps_period"]})
    finished_at = get_taipei_now().astimezone(TAIPEI_TZ).isoformat(timespec="seconds")
    manifest = _payload_manifest(payloads)

    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO fundamental_ingestion_runs (
                snapshot_date, started_at, finished_at, source_version, status,
                twse_records, tpex_records, merged_codes,
                persisted_observations, valuation_date, revenue_period,
                eps_period, payloads_json, warnings_json, git_commit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, source_version) DO UPDATE SET
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                status=excluded.status,
                twse_records=excluded.twse_records,
                tpex_records=excluded.tpex_records,
                merged_codes=excluded.merged_codes,
                valuation_date=excluded.valuation_date,
                revenue_period=excluded.revenue_period,
                eps_period=excluded.eps_period,
                payloads_json=excluded.payloads_json,
                warnings_json=excluded.warnings_json,
                git_commit=excluded.git_commit
            """,
            (
                snapshot_date,
                started_at,
                finished_at,
                FUNDAMENTAL_SOURCE_VERSION,
                status,
                twse_count,
                tpex_count,
                len(observations),
                valuation_dates[-1] if valuation_dates else None,
                revenue_periods[-1] if revenue_periods else None,
                eps_periods[-1] if eps_periods else None,
                json.dumps(manifest, sort_keys=True),
                json.dumps(warnings, ensure_ascii=False),
                get_git_commit(),
            ),
        )
        run_id = conn.execute(
            """
            SELECT id FROM fundamental_ingestion_runs
            WHERE snapshot_date=? AND source_version=?
            """,
            (snapshot_date, FUNDAMENTAL_SOURCE_VERSION),
        ).fetchone()[0]
        persisted = persist_observations(conn, run_id, observations, finished_at)
        conn.execute(
            """
            UPDATE fundamental_ingestion_runs
            SET persisted_observations=? WHERE id=?
            """,
            (persisted, run_id),
        )

    return {
        "status": status,
        "snapshotDate": snapshot_date,
        "sourceVersion": FUNDAMENTAL_SOURCE_VERSION,
        "twseCodes": twse_count,
        "tpexCodes": tpex_count,
        "observations": len(observations),
        "valuationDate": valuation_dates[-1] if valuation_dates else None,
        "revenuePeriod": revenue_periods[-1] if revenue_periods else None,
        "epsPeriod": eps_periods[-1] if eps_periods else None,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Capture official TWSE and TPEx fundamentals as point-in-time evidence."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--fail-on-empty", action="store_true")
    args = parser.parse_args()
    result = ingest_official_fundamentals(db_path=args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_empty and result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
