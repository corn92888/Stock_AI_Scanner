import argparse
import datetime as dt
import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from database import DB_PATH, get_connection, init_db


TAIPEI_TZ = ZoneInfo("Asia/Taipei")
MODEL_VERSION = "global_regime_shadow_v2"
TWSE_MARKET_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"


@dataclass(frozen=True)
class MarketInstrument:
    key: str
    symbol: str | None
    name: str
    group: str
    region: str
    asset_class: str
    currency: str
    impact_direction: float
    model_weight: float
    timezone: str
    session_open: str | None = None
    session_close: str | None = None
    source_name: str = "Yahoo Finance"
    source_tier: str = "fallback_delayed"


INSTRUMENTS = (
    MarketInstrument("taiex", "^TWII", "台灣加權指數", "台灣市場", "台灣", "index", "TWD", 0, 0, "Asia/Taipei", "09:00", "13:30"),
    MarketInstrument("otc", "^TWOII", "櫃買指數", "台灣市場", "台灣", "index", "TWD", 0, 0, "Asia/Taipei", "09:00", "13:30"),
    MarketInstrument("taifex_night", None, "台指期夜盤", "台灣市場", "台灣", "future", "TWD", 1, 0.14, "Asia/Taipei", source_name="待接授權資料源", source_tier="not_connected"),
    MarketInstrument("sp500_futures", "ES=F", "S&P 500 期貨", "美股風險", "美國", "future", "USD", 1, 0.10, "America/New_York"),
    MarketInstrument("nasdaq_futures", "NQ=F", "Nasdaq 100 期貨", "美股風險", "美國", "future", "USD", 1, 0.14, "America/New_York"),
    MarketInstrument("sox", "^SOX", "費城半導體", "美股風險", "美國", "index", "USD", 1, 0.16, "America/New_York", "09:30", "16:00"),
    MarketInstrument("vix", "^VIX", "VIX 恐慌指數", "美股風險", "美國", "index", "USD", -1, 0.10, "America/New_York", "09:30", "16:00"),
    MarketInstrument("tsm_adr", "TSM", "台積電 ADR", "美股風險", "美國", "equity", "USD", 1, 0.12, "America/New_York", "09:30", "16:00"),
    MarketInstrument("us10y", "^TNX", "美國 10 年債殖利率", "匯率與利率", "美國", "rate", "%", -1, 0.04, "America/New_York", "08:00", "17:00"),
    MarketInstrument("dxy", "DX-Y.NYB", "美元指數", "匯率與利率", "全球", "fx", "index", -1, 0.04, "America/New_York"),
    MarketInstrument("usd_twd", "TWD=X", "美元兌台幣", "匯率與利率", "台灣", "fx", "TWD", -1, 0.06, "Asia/Taipei"),
    MarketInstrument("usd_krw", "KRW=X", "美元兌韓元", "匯率與利率", "韓國", "fx", "KRW", -1, 0.03, "Asia/Seoul"),
    MarketInstrument("kospi", "^KS11", "韓國 KOSPI", "亞洲科技", "韓國", "index", "KRW", 1, 0.06, "Asia/Seoul", "09:00", "15:30"),
    MarketInstrument("kosdaq", "^KQ11", "韓國 KOSDAQ", "亞洲科技", "韓國", "index", "KRW", 1, 0.04, "Asia/Seoul", "09:00", "15:30"),
    MarketInstrument("samsung", "005930.KS", "Samsung Electronics", "亞洲科技", "韓國", "equity", "KRW", 1, 0.04, "Asia/Seoul", "09:00", "15:30"),
    MarketInstrument("sk_hynix", "000660.KS", "SK Hynix", "亞洲科技", "韓國", "equity", "KRW", 1, 0.06, "Asia/Seoul", "09:00", "15:30"),
    MarketInstrument("nikkei", "^N225", "日經 225", "亞洲科技", "日本", "index", "JPY", 1, 0.03, "Asia/Tokyo", "09:00", "15:30"),
    MarketInstrument("hang_seng", "^HSI", "香港恆生", "亞洲科技", "香港", "index", "HKD", 1, 0.02, "Asia/Hong_Kong", "09:30", "16:00"),
    MarketInstrument("wti", "CL=F", "WTI 原油期貨", "原物料", "全球", "commodity", "USD", -1, 0.02, "America/New_York"),
    MarketInstrument("copper", "HG=F", "銅期貨", "原物料", "全球", "commodity", "USD", 1, 0.03, "America/New_York"),
    MarketInstrument("gold", "GC=F", "黃金期貨", "原物料", "全球", "commodity", "USD", -1, 0.02, "America/New_York"),
)

COMPONENTS = {
    "us_risk": ("美股與半導體", {"sp500_futures", "nasdaq_futures", "sox", "vix", "tsm_adr"}),
    "asia_tech": ("亞洲科技鏈", {"kospi", "kosdaq", "samsung", "sk_hynix", "nikkei", "hang_seng"}),
    "macro": ("美元與利率", {"us10y", "dxy", "usd_twd", "usd_krw"}),
    "commodities": ("原物料壓力", {"wti", "copper", "gold"}),
}


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _iso_timestamp(value, fallback_timezone="UTC"):
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(fallback_timezone)
    return timestamp.isoformat()


def _market_date(value, timezone):
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone)
    else:
        timestamp = timestamp.tz_convert(timezone)
    return timestamp.date()


def _session_status(instrument, now):
    local = now.astimezone(ZoneInfo(instrument.timezone))
    if local.weekday() >= 5:
        return "closed"
    if not instrument.session_open or not instrument.session_close:
        return "open"
    opened = dt.time.fromisoformat(instrument.session_open)
    closed = dt.time.fromisoformat(instrument.session_close)
    return "open" if opened <= local.time().replace(tzinfo=None) <= closed else "closed"


def _split_download(frame, symbols):
    result = {}
    if frame is None or frame.empty:
        return result
    for symbol in symbols:
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                if symbol in frame.columns.get_level_values(0):
                    symbol_frame = frame[symbol].copy()
                elif symbol in frame.columns.get_level_values(1):
                    symbol_frame = frame.xs(symbol, axis=1, level=1).copy()
                else:
                    continue
            elif len(symbols) == 1:
                symbol_frame = frame.copy()
            else:
                continue
            if "Close" not in symbol_frame:
                continue
            result[symbol] = symbol_frame.dropna(how="all")
        except (KeyError, TypeError, ValueError):
            continue
    return result


class YFinanceProvider:
    def fetch(self, instruments):
        import yfinance as yf

        symbols = [instrument.symbol for instrument in instruments if instrument.symbol]
        common = {
            "tickers": " ".join(symbols),
            "group_by": "ticker",
            "auto_adjust": False,
            "actions": False,
            "threads": True,
            "progress": False,
            "timeout": 25,
        }
        daily = yf.download(period="6mo", interval="1d", **common)
        intraday = yf.download(period="5d", interval="15m", **common)
        return _split_download(daily, symbols), _split_download(intraday, symbols)


def _twse_number(value):
    return _finite(str(value).replace(",", "").replace("+", ""))


def _twse_date(value):
    year, month, day = (int(part) for part in str(value).split("/"))
    return dt.date(year + 1911, month, day)


class TwseOfficialCloseProvider:
    def fetch(self, as_of):
        if isinstance(as_of, dt.datetime):
            as_of = as_of.astimezone(TAIPEI_TZ).date()
        query = urllib.parse.urlencode(
            {"date": as_of.strftime("%Y%m%d"), "response": "json"}
        )
        request = urllib.request.Request(
            f"{TWSE_MARKET_URL}?{query}",
            headers={"User-Agent": "Stock-AI-Scanner/1.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("stat") != "OK":
            return {}

        fields = payload.get("fields") or []
        rows = payload.get("data") or []
        required = {"日期", "發行量加權股價指數", "漲跌點數"}
        if not required.issubset(fields):
            return {}
        field_index = {field: index for index, field in enumerate(fields)}
        eligible = []
        for row in rows:
            try:
                trade_date = _twse_date(row[field_index["日期"]])
            except (IndexError, TypeError, ValueError):
                continue
            if trade_date <= as_of:
                eligible.append((trade_date, row))
        if not eligible:
            return {}

        trade_date, row = max(eligible, key=lambda item: item[0])
        price = _twse_number(row[field_index["發行量加權股價指數"]])
        change = _twse_number(row[field_index["漲跌點數"]])
        if price is None or change is None:
            return {}
        previous_close = price - change
        pct_change = (
            change / previous_close * 100 if previous_close not in (None, 0) else None
        )
        market_at = dt.datetime.combine(
            trade_date,
            dt.time(13, 30),
            tzinfo=TAIPEI_TZ,
        )
        return {
            "taiex": {
                "marketAt": market_at.isoformat(),
                "price": price,
                "previousClose": previous_close,
                "pctChange": pct_change,
                "shockZ": pct_change / 1.5 if pct_change is not None else None,
                "sourceName": "TWSE official close",
                "sourceTier": "official_close",
                "dataStatus": "closed",
                "sessionStatus": "closed",
            }
        }


def apply_official_closes(observations, official_closes, now):
    official_closes = official_closes or {}
    for observation in observations:
        official = official_closes.get(observation["key"])
        if not official or not official.get("marketAt"):
            continue
        official_date = _market_date(official["marketAt"], "Asia/Taipei")
        observed_date = _market_date(observation.get("marketAt"), "Asia/Taipei")
        if observed_date is not None and official_date < observed_date:
            continue
        observation.update(official)
        market_time = dt.datetime.fromisoformat(official["marketAt"])
        observation["latencyMinutes"] = max(
            0,
            (
                now.astimezone(dt.timezone.utc)
                - market_time.astimezone(dt.timezone.utc)
            ).total_seconds()
            / 60,
        )
    return observations


def _observation(instrument, daily, intraday, now):
    daily = daily.copy() if daily is not None else pd.DataFrame()
    intraday = intraday.copy() if intraday is not None else pd.DataFrame()
    daily_close = daily.get("Close", pd.Series(dtype=float)).dropna()
    intraday_close = intraday.get("Close", pd.Series(dtype=float)).dropna()

    if not intraday_close.empty:
        price = _finite(intraday_close.iloc[-1])
        market_index = intraday_close.index[-1]
        market_at = _iso_timestamp(market_index, instrument.timezone)
    elif not daily_close.empty:
        price = _finite(daily_close.iloc[-1])
        market_index = daily_close.index[-1]
        market_at = _iso_timestamp(market_index, instrument.timezone)
    else:
        price = None
        market_index = None
        market_at = None

    previous_close = None
    if price is not None and len(daily_close) >= 1:
        latest_daily_date = _market_date(daily_close.index[-1], instrument.timezone)
        current_date = _market_date(market_index, instrument.timezone)
        if current_date == latest_daily_date and len(daily_close) >= 2:
            previous_close = _finite(daily_close.iloc[-2])
        else:
            previous_close = _finite(daily_close.iloc[-1])

    pct_change = None
    if price is not None and previous_close not in (None, 0):
        pct_change = (price / previous_close - 1) * 100

    return_5d = None
    if len(daily_close) >= 6 and daily_close.iloc[-6] != 0:
        return_5d = (daily_close.iloc[-1] / daily_close.iloc[-6] - 1) * 100

    shock_z = None
    returns = daily_close.pct_change(fill_method=None).mul(100).dropna()
    baseline = returns.iloc[-61:-1]
    if pct_change is not None and len(baseline) >= 20:
        std = _finite(baseline.std(ddof=0))
        if std and std > 1e-9:
            shock_z = (pct_change - float(baseline.mean())) / std
    if shock_z is None and pct_change is not None:
        shock_z = pct_change / 1.5

    volume = None
    if not intraday.empty and "Volume" in intraday:
        volume_series = intraday["Volume"].dropna()
        if not volume_series.empty:
            latest_date = _market_date(volume_series.index[-1], instrument.timezone)
            same_day = [
                _market_date(index, instrument.timezone) == latest_date
                for index in volume_series.index
            ]
            volume = _finite(volume_series.loc[same_day].sum())
    if volume is None and not daily.empty and "Volume" in daily:
        volume_series = daily["Volume"].dropna()
        volume = _finite(volume_series.iloc[-1]) if not volume_series.empty else None

    session_status = _session_status(instrument, now)
    latency_minutes = None
    if market_at:
        market_time = dt.datetime.fromisoformat(market_at)
        latency_minutes = max(0, (now.astimezone(dt.timezone.utc) - market_time.astimezone(dt.timezone.utc)).total_seconds() / 60)
    if price is None:
        data_status = "not_connected" if not instrument.symbol else "unavailable"
    elif session_status == "closed":
        data_status = "closed"
    elif latency_minutes is not None and latency_minutes <= 45:
        data_status = "fresh"
    elif latency_minutes is not None and latency_minutes <= 240:
        data_status = "delayed"
    else:
        data_status = "stale"

    return {
        "key": instrument.key,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "group": instrument.group,
        "region": instrument.region,
        "assetClass": instrument.asset_class,
        "currency": instrument.currency,
        "marketAt": market_at,
        "price": price,
        "previousClose": previous_close,
        "pctChange": _finite(pct_change),
        "return5d": _finite(return_5d),
        "shockZ": _finite(shock_z),
        "volume": volume,
        "sourceName": instrument.source_name,
        "sourceTier": instrument.source_tier,
        "dataStatus": data_status,
        "sessionStatus": session_status,
        "latencyMinutes": _finite(latency_minutes),
        "impactDirection": instrument.impact_direction,
        "modelWeight": instrument.model_weight,
        "impactPoints": 0.0,
    }


def _label(score, positive, neutral, negative):
    if score >= 62:
        return positive
    if score <= 38:
        return negative
    return neutral


def build_market_snapshot(
    daily_frames,
    intraday_frames,
    now=None,
    official_closes=None,
):
    now = now or dt.datetime.now(TAIPEI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    observations = [
        _observation(
            instrument,
            daily_frames.get(instrument.symbol) if instrument.symbol else None,
            intraday_frames.get(instrument.symbol) if instrument.symbol else None,
            now,
        )
        for instrument in INSTRUMENTS
    ]
    apply_official_closes(observations, official_closes, now)

    weighted = [
        row for row in observations
        if row["shockZ"] is not None and row["modelWeight"] > 0
    ]
    total_weight = sum(row["modelWeight"] for row in weighted)
    for row in weighted:
        normalized_shock = float(np.clip(row["shockZ"], -3, 3))
        row["impactPoints"] = (
            normalized_shock
            * row["impactDirection"]
            * row["modelWeight"]
            / total_weight
            * 12
            if total_weight
            else 0
        )
    score = float(np.clip(50 + sum(row["impactPoints"] for row in weighted), 0, 100))
    local_rows = [
        row
        for row in observations
        if row["key"] in {"taiex", "otc"} and row["shockZ"] is not None
    ]
    local_score = 50.0
    if local_rows:
        local_score = float(
            np.clip(
                50
                + np.mean(
                    [float(np.clip(row["shockZ"], -3, 3)) for row in local_rows]
                )
                * 8,
                0,
                100,
            )
        )
    taiwan_bias_score = float(np.clip(score * 0.5 + local_score * 0.5, 0, 100))

    components = []
    for key, (name, members) in COMPONENTS.items():
        rows = [row for row in weighted if row["key"] in members]
        component_weight = sum(row["modelWeight"] for row in rows)
        component_score = 50.0
        if component_weight:
            component_score = 50 + 12 * sum(
                float(np.clip(row["shockZ"], -3, 3))
                * row["impactDirection"]
                * row["modelWeight"]
                for row in rows
            ) / component_weight
        components.append({
            "key": key,
            "name": name,
            "score": float(np.clip(component_score, 0, 100)),
            "coverage": len(rows),
            "total": len(members),
        })

    drivers = []
    for row in sorted(weighted, key=lambda item: abs(item["impactPoints"]), reverse=True)[:6]:
        direction = "支撐" if row["impactPoints"] >= 0 else "壓抑"
        drivers.append({
            "key": row["key"],
            "name": row["name"],
            "impactPoints": row["impactPoints"],
            "pctChange": row["pctChange"],
            "tone": "positive" if row["impactPoints"] >= 0 else "negative",
            "reason": f"{row['name']} 的價格衝擊目前{direction}台股風險偏好",
        })

    available = [row for row in observations if row["price"] is not None]
    connected = [row for row in observations if row["sourceTier"] != "not_connected"]
    active = [row for row in connected if row["sessionStatus"] == "open"]
    active_fresh = [row for row in active if row["dataStatus"] in {"fresh", "delayed"}]
    coverage_pct = len(available) / len(observations) * 100
    active_fresh_pct = len(active_fresh) / len(active) * 100 if active else 100.0
    missing = [row["key"] for row in observations if row["price"] is None]
    warnings = []
    if "taifex_night" in missing:
        warnings.append("台指期夜盤尚未接入授權行情，未納入風險分數。")
    has_official_close = any(
        row["sourceTier"] == "official_close" for row in observations
    )
    if has_official_close:
        warnings.append(
            "台灣加權收盤採 TWSE 官方資料；其餘跨市場行情仍使用延遲備援資料。"
        )
    else:
        warnings.append(
            "目前跨市場行情使用延遲備援資料，只供研究情境判讀，不直接改寫正式選股排名。"
        )

    return {
        "modelVersion": MODEL_VERSION,
        "snapshotAt": now.astimezone(TAIPEI_TZ).isoformat(timespec="seconds"),
        "score": score,
        "regimeLabel": _label(score, "風險偏多", "中性盤整", "風險偏空"),
        "taiwanBiasScore": taiwan_bias_score,
        "taiwanBiasLabel": _label(
            taiwan_bias_score,
            "正向",
            "中性",
            "負向",
        ),
        "components": components,
        "drivers": drivers,
        "instruments": observations,
        "quality": {
            "status": (
                "official_close_plus_fallback"
                if has_official_close
                else "fallback_delayed"
            ),
            "coveragePct": coverage_pct,
            "activeFreshPct": active_fresh_pct,
            "taiwanLocalScore": local_score,
            "available": len(available),
            "total": len(observations),
            "missingKeys": missing,
            "warnings": warnings,
            "formalRankingEnabled": False,
        },
    }


def collect_global_market(provider=None, now=None, official_provider=None):
    now = now or dt.datetime.now(TAIPEI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    provider = provider or YFinanceProvider()
    daily_frames, intraday_frames = provider.fetch(INSTRUMENTS)
    official_closes = {}
    try:
        official_provider = official_provider or TwseOfficialCloseProvider()
        official_closes = official_provider.fetch(now)
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        print(f"TWSE official close unavailable: {exc}")
    snapshot = build_market_snapshot(
        daily_frames,
        intraday_frames,
        now=now,
        official_closes=official_closes,
    )
    if not any(row["price"] is not None for row in snapshot["instruments"]):
        raise RuntimeError("Global market provider returned no usable prices")
    return snapshot


def persist_global_market(snapshot, db_path=DB_PATH):
    created_at = dt.datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")
    registry = {instrument.key: instrument for instrument in INSTRUMENTS}
    with get_connection(db_path) as conn:
        init_db(conn)
        for row in snapshot["instruments"]:
            instrument = registry[row["key"]]
            conn.execute(
                """
                INSERT INTO market_instruments (
                    instrument_key, symbol, display_name, group_name, region,
                    asset_class, currency, source_name, source_tier,
                    impact_direction, model_weight, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_key) DO UPDATE SET
                    symbol=excluded.symbol, display_name=excluded.display_name,
                    group_name=excluded.group_name, region=excluded.region,
                    asset_class=excluded.asset_class, currency=excluded.currency,
                    source_name=excluded.source_name, source_tier=excluded.source_tier,
                    impact_direction=excluded.impact_direction,
                    model_weight=excluded.model_weight,
                    metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
                """,
                (
                    instrument.key, instrument.symbol, instrument.name, instrument.group,
                    instrument.region, instrument.asset_class, instrument.currency,
                    row["sourceName"], row["sourceTier"],
                    instrument.impact_direction, instrument.model_weight,
                    json.dumps({"timezone": instrument.timezone,
                                "session_open": instrument.session_open,
                                "session_close": instrument.session_close}),
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO market_observations (
                    snapshot_at, instrument_key, market_at, price, previous_close,
                    pct_change, return_5d, shock_z, volume, source_name,
                    source_tier, data_status, session_status, latency_minutes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["snapshotAt"], row["key"], row["marketAt"], row["price"],
                    row["previousClose"], row["pctChange"], row["return5d"], row["shockZ"],
                    row["volume"], row["sourceName"], row["sourceTier"], row["dataStatus"],
                    row["sessionStatus"], row["latencyMinutes"], created_at,
                ),
            )
        quality = snapshot["quality"]
        conn.execute(
            """
            INSERT OR REPLACE INTO market_regime_snapshots (
                snapshot_at, score, regime_label, taiwan_bias_score,
                taiwan_bias_label, coverage_pct, active_fresh_pct,
                components_json, drivers_json, quality_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["snapshotAt"], snapshot["score"], snapshot["regimeLabel"],
                snapshot["taiwanBiasScore"], snapshot["taiwanBiasLabel"],
                quality["coveragePct"], quality["activeFreshPct"],
                json.dumps(snapshot["components"], ensure_ascii=False),
                json.dumps(snapshot["drivers"], ensure_ascii=False),
                json.dumps(quality, ensure_ascii=False), created_at,
            ),
        )
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=180)).isoformat()
        conn.execute("DELETE FROM market_observations WHERE snapshot_at < ?", (cutoff,))
        conn.execute("DELETE FROM market_regime_snapshots WHERE snapshot_at < ?", (cutoff,))


def sync_supabase(snapshot):
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return False
    import requests

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    def upsert(table, rows, conflict):
        response = requests.post(
            f"{url}/rest/v1/{table}?on_conflict={conflict}",
            headers=headers,
            json=rows,
            timeout=20,
        )
        response.raise_for_status()

    registry = {instrument.key: instrument for instrument in INSTRUMENTS}
    upsert(
        "market_instruments",
        [
            {
                "instrument_key": row["key"], "symbol": row["symbol"],
                "display_name": row["name"], "group_name": row["group"],
                "region": row["region"], "asset_class": row["assetClass"],
                "currency": row["currency"], "source_name": row["sourceName"],
                "source_tier": row["sourceTier"],
                "impact_direction": registry[row["key"]].impact_direction,
                "model_weight": registry[row["key"]].model_weight,
                "metadata": asdict(registry[row["key"]]),
                "updated_at": snapshot["snapshotAt"],
            }
            for row in snapshot["instruments"]
        ],
        "instrument_key",
    )
    upsert(
        "market_observations",
        [
            {
                "snapshot_at": snapshot["snapshotAt"], "instrument_key": row["key"],
                "market_at": row["marketAt"], "price": row["price"],
                "previous_close": row["previousClose"], "pct_change": row["pctChange"],
                "return_5d": row["return5d"], "shock_z": row["shockZ"],
                "volume": row["volume"], "source_name": row["sourceName"],
                "source_tier": row["sourceTier"], "data_status": row["dataStatus"],
                "session_status": row["sessionStatus"],
                "latency_minutes": row["latencyMinutes"],
            }
            for row in snapshot["instruments"]
        ],
        "snapshot_at,instrument_key",
    )
    upsert(
        "market_regime_snapshots",
        [{
            "snapshot_at": snapshot["snapshotAt"], "score": snapshot["score"],
            "regime_label": snapshot["regimeLabel"],
            "taiwan_bias_score": snapshot["taiwanBiasScore"],
            "taiwan_bias_label": snapshot["taiwanBiasLabel"],
            "coverage_pct": snapshot["quality"]["coveragePct"],
            "active_fresh_pct": snapshot["quality"]["activeFreshPct"],
            "components": snapshot["components"], "drivers": snapshot["drivers"],
            "quality": snapshot["quality"],
        }],
        "snapshot_at",
    )
    return True


def main():
    parser = argparse.ArgumentParser(description="Collect normalized cross-market risk context.")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--output", default="data/global_market_latest.json")
    parser.add_argument("--no-supabase", action="store_true")
    args = parser.parse_args()

    snapshot = collect_global_market()
    persist_global_market(snapshot, args.db_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    synced = False
    if not args.no_supabase:
        try:
            synced = sync_supabase(snapshot)
        except Exception as exc:
            print(f"Supabase market sync skipped after error: {exc}")
    print(
        f"Global market collected: score={snapshot['score']:.1f} "
        f"coverage={snapshot['quality']['available']}/{snapshot['quality']['total']} "
        f"supabase={'synced' if synced else 'disabled'}"
    )


if __name__ == "__main__":
    main()
