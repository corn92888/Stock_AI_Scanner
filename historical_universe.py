import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import requests


SCHEMA_VERSION = "official_point_in_time_universe_v1"
DEFAULT_OUTPUT = Path("data/universe_history.csv")
OFFICIAL_SOURCES = {
    "twse_current": {
        "label": "TWSE listed company master",
        "method": "GET",
        "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    },
    "twse_listings": {
        "label": "TWSE historical new listings",
        "method": "GET",
        "url": "https://www.twse.com.tw/rwd/zh/company/newlisting?response=json",
    },
    "twse_delisted": {
        "label": "TWSE suspended listings",
        "method": "GET",
        "url": "https://www.twse.com.tw/rwd/zh/company/suspendListing?response=json&startYear=",
    },
    "tpex_current": {
        "label": "TPEx listed company master",
        "method": "GET",
        "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    },
    "tpex_listings": {
        "label": "TPEx historical new listings",
        "method": "POST",
        "url": "https://www.tpex.org.tw/www/zh-tw/company/latest",
        "data": {"code": "", "date": "ALL", "response": "json"},
    },
    "tpex_delisted": {
        "label": "TPEx terminated listings",
        "method": "POST",
        "url": "https://www.tpex.org.tw/www/zh-tw/company/deListed",
        "data": {
            "code": "",
            "date": "ALL",
            "reason": "-1",
            "response": "json",
            "paging-offset": "0",
            "paging-size": "5000",
        },
    },
}

INDUSTRY_LABELS = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "19": "綜合企業",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療",
    "23": "油電燃氣",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "34": "電子商務業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


@dataclass(frozen=True)
class SecurityMembership:
    code: str
    name: str
    industry: str
    industry_code: str
    market: str
    listed_on: dt.date
    delisted_on: dt.date | None
    membership_quality: str
    status: str
    source: str
    delisting_reason: str = ""

    def to_record(self):
        record = asdict(self)
        record["listed_on"] = self.listed_on.isoformat()
        record["delisted_on"] = (
            self.delisted_on.isoformat() if self.delisted_on else ""
        )
        return record


def normalize_code(value):
    code = str(value or "").strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def parse_official_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    groups = re.findall(r"\d+", text)
    if len(groups) == 1:
        digits = groups[0]
        if len(digits) == 7:
            parts = [int(digits[:3]), int(digits[3:5]), int(digits[5:])]
        elif len(digits) == 8:
            parts = [int(digits[:4]), int(digits[4:6]), int(digits[6:])]
        else:
            parts = [int(digits)]
    else:
        parts = [int(part) for part in groups]
    if len(parts) != 3:
        raise ValueError(f"Unsupported official date: {value!r}")
    year, month, day = parts
    if year < 1911:
        year += 1911
    return dt.date(year, month, day)


def _payload_row_count(payload):
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if isinstance(payload.get("data"), list):
        return len(payload["data"])
    tables = payload.get("tables") or []
    return len(tables[0].get("data") or []) if tables else 0


def _request_json(session, source_key, timeout, attempts=3):
    spec = OFFICIAL_SOURCES[source_key]
    error = None
    for attempt in range(attempts):
        try:
            response = session.request(
                spec["method"],
                spec["url"],
                data=spec.get("data"),
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as exc:
            error = exc
            try:
                return _curl_json(spec, timeout)
            except (subprocess.SubprocessError, ValueError) as curl_exc:
                error = curl_exc
                break
        except (requests.RequestException, ValueError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {source_key}: {error}") from error


def _curl_json(spec, timeout):
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        str(timeout),
        "--request",
        spec["method"],
        spec["url"],
    ]
    for key, value in (spec.get("data") or {}).items():
        command.extend(["--data-urlencode", f"{key}={value}"])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    return json.loads(result.stdout)


def fetch_official_payloads(timeout=30):
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Stock-AI-Scanner-Historical-Universe/1.0",
        }
    )
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payloads = {}
    source_records = []
    for key, spec in OFFICIAL_SOURCES.items():
        payload = _request_json(session, key, timeout)
        payloads[key] = payload
        source_records.append(
            {
                "key": key,
                "label": spec["label"],
                "url": spec["url"],
                "method": spec["method"],
                "retrieved_at": retrieved_at,
                "row_count": _payload_row_count(payload),
            }
        )
    return payloads, source_records


def _table_records(payload):
    tables = payload.get("tables") or []
    if not tables:
        return []
    table = tables[0]
    fields = table.get("fields") or []
    return [dict(zip(fields, row)) for row in table.get("data") or []]


def _validate_official_payloads(payloads):
    missing = [key for key in OFFICIAL_SOURCES if key not in payloads]
    if missing:
        raise ValueError(f"Official universe payloads missing: {', '.join(missing)}")
    empty = [key for key in OFFICIAL_SOURCES if _payload_row_count(payloads[key]) == 0]
    if empty:
        raise RuntimeError(
            f"Official universe sources returned no rows: {', '.join(empty)}"
        )


def _twse_records(payload):
    fields = payload.get("fields") or []
    return [dict(zip(fields, row)) for row in payload.get("data") or []]


def _listing_history(payloads):
    history = {"上市": defaultdict(list), "上櫃": defaultdict(list)}
    for row in _twse_records(payloads["twse_listings"]):
        code = normalize_code(row.get("公司代號"))
        listed_on = parse_official_date(row.get("股票上市買賣日期"))
        if code and listed_on:
            history["上市"][code].append(listed_on)
    for row in _table_records(payloads["tpex_listings"]):
        code = normalize_code(row.get("股票代號"))
        listed_on = parse_official_date(row.get("上櫃日期"))
        if code and listed_on:
            history["上櫃"][code].append(listed_on)
    for market in history:
        for code in history[market]:
            history[market][code] = sorted(set(history[market][code]))
    return history


def _current_memberships(payloads):
    memberships = []
    for row in payloads["twse_current"]:
        code = normalize_code(row.get("公司代號"))
        listed_on = parse_official_date(row.get("上市日期"))
        if not code or not listed_on:
            continue
        raw_industry_code = str(row.get("產業別") or "").strip()
        industry_code = raw_industry_code.zfill(2) if raw_industry_code else ""
        memberships.append(
            SecurityMembership(
                code=code,
                name=str(row.get("公司簡稱") or row.get("公司名稱") or code).strip(),
                industry=(
                    INDUSTRY_LABELS.get(industry_code, f"產業代碼 {industry_code}")
                    if industry_code
                    else "未分類"
                ),
                industry_code=industry_code,
                market="上市",
                listed_on=listed_on,
                delisted_on=None,
                membership_quality="exact",
                status="current",
                source="twse_current",
            )
        )
    for row in payloads["tpex_current"]:
        code = normalize_code(row.get("SecuritiesCompanyCode"))
        listed_on = parse_official_date(row.get("DateOfListing"))
        if not code or not listed_on:
            continue
        raw_industry_code = str(row.get("SecuritiesIndustryCode") or "").strip()
        industry_code = raw_industry_code.zfill(2) if raw_industry_code else ""
        memberships.append(
            SecurityMembership(
                code=code,
                name=str(
                    row.get("CompanyAbbreviation") or row.get("CompanyName") or code
                ).strip(),
                industry=(
                    INDUSTRY_LABELS.get(industry_code, f"產業代碼 {industry_code}")
                    if industry_code
                    else "未分類"
                ),
                industry_code=industry_code,
                market="上櫃",
                listed_on=listed_on,
                delisted_on=None,
                membership_quality="exact",
                status="current",
                source="tpex_current",
            )
        )
    return memberships


def _best_listing_date(history, market, code, delisted_on):
    candidates = [
        listed_on
        for listed_on in history[market].get(code, [])
        if listed_on < delisted_on
    ]
    return max(candidates) if candidates else None


def _delisted_memberships(payloads, history, coverage_start):
    memberships = []
    for raw in payloads["twse_delisted"].get("data") or []:
        delisted_on = parse_official_date(raw[0])
        code = normalize_code(raw[2])
        listed_on = _best_listing_date(history, "上市", code, delisted_on)
        quality = "exact" if listed_on else "partial_start"
        memberships.append(
            SecurityMembership(
                code=code,
                name=str(raw[1] or code).strip(),
                industry="未分類",
                industry_code="",
                market="上市",
                listed_on=listed_on or coverage_start,
                delisted_on=delisted_on,
                membership_quality=quality,
                status="delisted",
                source="twse_delisted+twse_listings",
            )
        )
    for row in _table_records(payloads["tpex_delisted"]):
        delisted_on = parse_official_date(row.get("終止上櫃日期"))
        code = normalize_code(row.get("股票代號"))
        listed_on = _best_listing_date(history, "上櫃", code, delisted_on)
        quality = "exact" if listed_on else "partial_start"
        memberships.append(
            SecurityMembership(
                code=code,
                name=str(row.get("公司名稱") or code).strip(),
                industry="未分類",
                industry_code="",
                market="上櫃",
                listed_on=listed_on or coverage_start,
                delisted_on=delisted_on,
                membership_quality=quality,
                status="delisted",
                source="tpex_delisted+tpex_listings",
                delisting_reason=str(row.get("終止上櫃原因") or "").strip(),
            )
        )
    return memberships


def _validate_memberships(memberships):
    grouped = defaultdict(list)
    for membership in memberships:
        if not membership.code or not membership.name:
            raise ValueError("Universe membership is missing a code or name.")
        if membership.delisted_on and membership.listed_on >= membership.delisted_on:
            raise ValueError(
                f"Invalid membership interval: {membership.market} {membership.code}"
            )
        grouped[(membership.market, membership.code)].append(membership)
    for (market, code), rows in grouped.items():
        rows.sort(key=lambda row: row.listed_on)
        for previous, current in zip(rows, rows[1:]):
            if previous.delisted_on is None or current.listed_on < previous.delisted_on:
                raise ValueError(f"Overlapping membership intervals: {market} {code}")


def build_universe_history(start_date, end_date, payloads, source_records=None):
    start = parse_official_date(start_date)
    end = parse_official_date(end_date)
    if start > end:
        raise ValueError("Universe start_date must be on or before end_date.")
    _validate_official_payloads(payloads)
    history = _listing_history(payloads)
    memberships = _current_memberships(payloads)
    memberships.extend(_delisted_memberships(payloads, history, start))
    memberships = [
        membership
        for membership in memberships
        if membership.listed_on <= end
        and (membership.delisted_on is None or membership.delisted_on > start)
    ]
    unique = {}
    for membership in memberships:
        key = (
            membership.market,
            membership.code,
            membership.listed_on,
            membership.delisted_on,
        )
        unique[key] = membership
    memberships = sorted(
        unique.values(),
        key=lambda row: (row.code, row.listed_on, row.market, row.delisted_on or dt.date.max),
    )
    _validate_memberships(memberships)
    records = [membership.to_record() for membership in memberships]
    frame = pd.DataFrame.from_records(records)
    partial = sum(row.membership_quality != "exact" for row in memberships)
    codes_by_market = defaultdict(set)
    markets_by_code = defaultdict(set)
    for row in memberships:
        codes_by_market[row.market].add(row.code)
        markets_by_code[row.code].add(row.market)
    transfer_codes = sorted(code for code, markets in markets_by_code.items() if len(markets) > 1)
    warnings = [
        "Industry classifications are the latest official classification for active companies; delisted companies without point-in-time classifications are marked unclassified.",
    ]
    if partial:
        warnings.append(
            f"{partial} delisted membership intervals predate the available official listing-history feed; their start is conservatively truncated to the requested coverage start."
        )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "coverage": {"start": start.isoformat(), "end": end.isoformat()},
        "effective_date_policy": "listed_on_inclusive_delisted_on_exclusive",
        "row_count": len(memberships),
        "unique_symbols": len(markets_by_code),
        "market_counts": {
            market: len(codes) for market, codes in sorted(codes_by_market.items())
        },
        "transfer_symbols": len(transfer_codes),
        "transfer_codes": transfer_codes,
        "membership_quality": {
            "status": "partial" if partial else "verified",
            "exact_intervals": len(memberships) - partial,
            "partial_start_intervals": partial,
        },
        "classification_quality": {
            "status": "latest_known",
            "unclassified_intervals": sum(not row.industry_code for row in memberships),
        },
        "sources": source_records or [
            {
                "key": key,
                "label": OFFICIAL_SOURCES[key]["label"],
                "url": OFFICIAL_SOURCES[key]["url"],
                "method": OFFICIAL_SOURCES[key]["method"],
                "row_count": _payload_row_count(payloads[key]),
            }
            for key in OFFICIAL_SOURCES
        ],
        "warnings": warnings,
    }
    return frame, metadata


def write_universe_artifacts(frame, metadata, output_path, metadata_path=None):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if metadata_path:
        metadata_output = Path(metadata_path)
    else:
        metadata_base = output.with_suffix("") if output.suffix == ".gz" else output
        metadata_output = metadata_base.with_suffix(".metadata.json")
    csv_temp = output.with_suffix(output.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if output.suffix == ".gz" else None
    frame.to_csv(
        csv_temp,
        index=False,
        encoding="utf-8",
        compression=compression,
    )
    csv_temp.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    document = {**metadata, "data_file": output.name, "data_sha256": digest}
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_temp = metadata_output.with_suffix(metadata_output.suffix + ".tmp")
    metadata_temp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metadata_temp.replace(metadata_output)
    return document


def main():
    parser = argparse.ArgumentParser(
        description="Build an official point-in-time TWSE and TPEx equity universe."
    )
    parser.add_argument("--start", required=True, dest="start_date")
    parser.add_argument("--end", required=True, dest="end_date")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--metadata-output")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    payloads, sources = fetch_official_payloads(timeout=args.timeout)
    frame, metadata = build_universe_history(
        args.start_date, args.end_date, payloads, source_records=sources
    )
    document = write_universe_artifacts(
        frame, metadata, args.output, args.metadata_output
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "metadata_output": str(
                    args.metadata_output
                    or (
                        Path(args.output).with_suffix("")
                        if Path(args.output).suffix == ".gz"
                        else Path(args.output)
                    ).with_suffix(".metadata.json")
                ),
                "row_count": document["row_count"],
                "unique_symbols": document["unique_symbols"],
                "membership_quality": document["membership_quality"],
                "transfer_symbols": document["transfer_symbols"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
