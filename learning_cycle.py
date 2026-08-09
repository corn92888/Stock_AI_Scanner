import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from database import (
    ALPHA_FORWARD_START_DATE,
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    LEARNING_CYCLE_VERSION,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)


PRIMARY_ACCOUNT_KEY = "alpha_v2_champion_forward_t10_v1"
CONTROL_ACCOUNT_KEYS = (
    PRIMARY_ACCOUNT_KEY,
    "alpha_v2_anti_chase_t10_v1",
    "alpha_v2_market_regime_t10_v1",
    "alpha_v2_momentum_t10_v1",
    "alpha_v2_random_t10_v1",
)
DEFAULT_REPORT_DIR = Path("Reports/research_cycles")
RECENT_TRADE_DATES = 60
MAX_ABSOLUTE_T3_RETURN_PCT = 50.0
MAX_ABSOLUTE_T3_EXCESS_PCT = 60.0


DIMENSION_LABELS = {
    "strategy": "策略來源",
    "volume": "五日量比",
    "turnover": "成交值",
    "score": "規則分數",
    "extension": "當日漲幅",
    "intraday_position": "日內收位",
    "defense_distance": "防守距離",
    "market_regime": "市場狀態",
    "industry_breadth": "產業廣度",
    "industry": "產業",
}


def _decode(value, default):
    try:
        decoded = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values):
    clean = [value for value in (_number(item) for item in values) if value is not None]
    return statistics.fmean(clean) if clean else None


def _round(value, digits=4):
    number = _number(value)
    return None if number is None else round(number, digits)


def _latest_trade_date(conn, as_of=None):
    if as_of:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM scan_runs WHERE trade_date <= ?",
            (str(as_of),),
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(trade_date) FROM scan_runs").fetchone()
    return str(row[0]) if row and row[0] else None


def _recent_window(conn, cycle_date, limit=RECENT_TRADE_DATES):
    rows = conn.execute(
        """
        SELECT DISTINCT sr.trade_date
        FROM scan_runs sr
        JOIN candidate_events ce ON ce.run_id=sr.id
        JOIN candidate_outcomes co ON co.candidate_id=ce.id
        WHERE sr.trade_date <= ?
          AND co.execution_version=?
          AND co.entry_status='filled'
          AND co.matured_horizon >= 3
          AND co.net_return_3d IS NOT NULL
        ORDER BY sr.trade_date DESC
        LIMIT ?
        """,
        (cycle_date, CANDIDATE_EXECUTION_VERSION, int(limit)),
    ).fetchall()
    dates = sorted(str(row[0]) for row in rows)
    return (dates[0], dates[-1]) if dates else (None, None)


def _candidate_outcomes(conn, start_date, end_date):
    if not start_date or not end_date:
        return []
    rows = conn.execute(
        """
        WITH canonical AS (
            SELECT ce.*, sr.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.trade_date, ce.code
                       ORDER BY ce.is_selected DESC,
                                ce.is_first_eligible_event DESC,
                                ce.as_of ASC, ce.id ASC
                   ) AS day_code_rank
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            WHERE sr.trade_date BETWEEN ? AND ?
        )
        SELECT canonical.*, co.net_return_3d, co.excess_return_3d,
               co.max_drawdown_3d, co.success_t3, co.evaluated_at
        FROM canonical
        JOIN candidate_outcomes co ON co.candidate_id=canonical.id
        WHERE canonical.day_code_rank=1
          AND co.execution_version=?
          AND co.entry_status='filled'
          AND co.matured_horizon >= 3
          AND co.net_return_3d IS NOT NULL
        ORDER BY canonical.trade_date, canonical.code
        """,
        (start_date, end_date, CANDIDATE_EXECUTION_VERSION),
    ).fetchall()
    return [dict(row) for row in rows]


def _bucket(value, cuts, labels, missing=("missing", "缺少資料")):
    number = _number(value)
    if number is None:
        return missing
    for boundary, key, label in cuts:
        if number < boundary:
            return key, label
    return labels


def _market_regime(snapshot):
    market_return = _number(snapshot.get("市場平均漲跌幅"))
    market_up = _number(snapshot.get("市場上漲比例"))
    if market_return is None or market_up is None:
        return "unknown", "資料不足"
    if market_return > 0 and market_up >= 50:
        return "supportive", "多方廣度"
    if market_return < 0 and market_up < 40:
        return "risk_off", "風險收縮"
    return "mixed", "分歧盤"


def _dimension_buckets(row):
    snapshot = _decode(row.get("snapshot_json"), {})
    strategies = _decode(row.get("strategies_json"), [])
    strategy = "+".join(sorted(str(item) for item in strategies if item)) or "unknown"
    industry = str(row.get("industry") or "未分類")
    return {
        "strategy": (strategy, strategy),
        "volume": _bucket(
            row.get("volume_ratio_5"),
            (
                (0.8, "lt_0_8", "低於 0.8x"),
                (1.2, "0_8_to_1_2", "0.8x 至 1.2x"),
                (2.0, "1_2_to_2", "1.2x 至 2x"),
                (3.5, "2_to_3_5", "2x 至 3.5x"),
            ),
            ("gte_3_5", "高於 3.5x"),
        ),
        "turnover": _bucket(
            row.get("turnover_billion"),
            (
                (5.0, "lt_5", "低於 5 億"),
                (10.0, "5_to_10", "5 至 10 億"),
                (30.0, "10_to_30", "10 至 30 億"),
            ),
            ("gte_30", "30 億以上"),
        ),
        "score": _bucket(
            row.get("score"),
            ((50.0, "lt_50", "低於 50"), (65.0, "50_to_65", "50 至 65"), (80.0, "65_to_80", "65 至 80")),
            ("gte_80", "80 以上"),
        ),
        "extension": _bucket(
            row.get("pct_change"),
            ((0.0, "negative", "當日下跌"), (3.0, "0_to_3", "上漲 0% 至 3%"), (6.0, "3_to_6", "上漲 3% 至 6%")),
            ("gte_6", "上漲 6% 以上"),
        ),
        "intraday_position": _bucket(
            row.get("intraday_position"),
            ((0.35, "weak", "收位低於 0.35"), (0.7, "middle", "收位 0.35 至 0.7")),
            ("strong", "收位高於 0.7"),
        ),
        "defense_distance": _bucket(
            row.get("stop_distance_pct"),
            ((4.0, "lt_4", "低於 4%"), (8.0, "4_to_8", "4% 至 8%"), (12.0, "8_to_12", "8% 至 12%")),
            ("gte_12", "12% 以上"),
        ),
        "market_regime": _market_regime(snapshot),
        "industry_breadth": _bucket(
            snapshot.get("產業上漲比例"),
            ((40.0, "weak", "低於 40%"), (60.0, "mixed", "40% 至 60%")),
            ("strong", "60% 以上"),
        ),
        "industry": (industry, industry),
    }


def _aggregate_bucket(rows, scope, dimension, bucket_key, bucket_label):
    net = [_number(row.get("net_return_3d")) for row in rows]
    net = [value for value in net if value is not None]
    excess = [_number(row.get("excess_return_3d")) for row in rows]
    excess = [value for value in excess if value is not None]
    drawdowns = [_number(row.get("max_drawdown_3d")) for row in rows]
    drawdowns = [value for value in drawdowns if value is not None]
    mean_net = statistics.fmean(net) if net else None
    standard_error = (
        statistics.stdev(net) / math.sqrt(len(net)) if len(net) >= 2 else None
    )
    return {
        "scope": scope,
        "dimension": dimension,
        "dimensionLabel": DIMENSION_LABELS[dimension],
        "bucketKey": bucket_key,
        "bucketLabel": bucket_label,
        "sampleCount": len(net),
        "meanNetReturn": _round(mean_net),
        "meanExcessReturn": _round(_mean(excess)),
        "positiveRate": _round(
            sum(value > 0 for value in net) / len(net) * 100 if net else None, 2
        ),
        "meanDrawdown": _round(_mean(drawdowns)),
        "standardError": _round(standard_error),
        "ci95Low": _round(mean_net - 1.96 * standard_error)
        if mean_net is not None and standard_error is not None
        else None,
        "ci95High": _round(mean_net + 1.96 * standard_error)
        if mean_net is not None and standard_error is not None
        else None,
        "negativeReturnTotal": _round(sum(min(value, 0.0) for value in net)),
        "codes": sorted({str(row.get("code") or "") for row in rows if row.get("code")}),
    }


def build_failure_attributions(rows):
    grouped = defaultdict(list)
    for row in rows:
        scope = "formal_selected" if bool(row.get("is_selected")) else "formal_rejected"
        for dimension, (bucket_key, bucket_label) in _dimension_buckets(row).items():
            grouped[(scope, dimension, bucket_key, bucket_label)].append(row)

    records = []
    for (scope, dimension, bucket_key, bucket_label), bucket_rows in grouped.items():
        records.append(
            _aggregate_bucket(bucket_rows, scope, dimension, bucket_key, bucket_label)
        )

    for scope in {record["scope"] for record in records}:
        for dimension in DIMENSION_LABELS:
            dimension_rows = [
                record
                for record in records
                if record["scope"] == scope and record["dimension"] == dimension
            ]
            total_loss = abs(
                sum(float(record.get("negativeReturnTotal") or 0.0) for record in dimension_rows)
            )
            dimension_rows.sort(
                key=lambda record: (
                    float(record.get("meanNetReturn") or 0.0),
                    -int(record.get("sampleCount") or 0),
                )
            )
            for index, record in enumerate(dimension_rows, start=1):
                record["sortOrder"] = index
                record["lossContribution"] = _round(
                    abs(float(record.get("negativeReturnTotal") or 0.0))
                    / total_loss
                    * 100
                    if total_loss
                    else 0.0,
                    2,
                )
    return records


def _scope_summary(rows, selected):
    scope = [row for row in rows if bool(row.get("is_selected")) is selected]
    net = [_number(row.get("net_return_3d")) for row in scope]
    net = [value for value in net if value is not None]
    excess = [_number(row.get("excess_return_3d")) for row in scope]
    excess = [value for value in excess if value is not None]
    return {
        "samples": len(net),
        "meanNetReturn": _round(_mean(net)),
        "meanExcessReturn": _round(_mean(excess)),
        "positiveRate": _round(
            sum(value > 0 for value in net) / len(net) * 100 if net else None, 2
        ),
    }


def _paper_accounts(conn):
    placeholders = ",".join("?" for _ in CONTROL_ACCOUNT_KEYS)
    rows = conn.execute(
        f"""
        SELECT pa.account_key, pa.name, pa.total_return_pct, pa.max_drawdown_pct,
               pa.closed_trades, pa.winning_trades, pa.open_positions,
               AVG(CASE WHEN pt.status='closed' THEN pt.net_return_pct END) AS mean_net_return,
               AVG(CASE WHEN pt.status='closed' THEN pt.excess_return_pct END) AS mean_excess_return
        FROM paper_accounts pa
        LEFT JOIN paper_trades pt ON pt.account_id=pa.id
        WHERE pa.account_key IN ({placeholders})
        GROUP BY pa.id
        ORDER BY CASE pa.account_key
            WHEN ? THEN 0 ELSE 1 END, pa.total_return_pct DESC
        """,
        (*CONTROL_ACCOUNT_KEYS, PRIMARY_ACCOUNT_KEY),
    ).fetchall()
    return [
        {
            "accountKey": row["account_key"],
            "name": row["name"],
            "totalReturnPct": _round(row["total_return_pct"]),
            "maxDrawdownPct": _round(row["max_drawdown_pct"]),
            "closedTrades": int(row["closed_trades"] or 0),
            "winningTrades": int(row["winning_trades"] or 0),
            "openPositions": int(row["open_positions"] or 0),
            "meanNetReturn": _round(row["mean_net_return"]),
            "meanExcessReturn": _round(row["mean_excess_return"]),
        }
        for row in rows
    ]


def _latest_json_snapshot(conn, table, time_column):
    row = conn.execute(
        f"SELECT metrics_json FROM {table} ORDER BY {time_column} DESC, id DESC LIMIT 1"
    ).fetchone()
    return _decode(row[0], {}) if row else {}


def _latest_model_challenger(conn):
    row = conn.execute(
        """
        SELECT model_version, evaluated_at, status, oof_trade_dates,
               challenger_trades, challenger_mean_net_return,
               challenger_mean_excess_return, excess_return_lift,
               challenger_max_drawdown, profitable_fold_rate, qualified,
               rejection_reasons_json
        FROM model_challenger_evaluations
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {}
    payload = dict(row)
    payload["rejectionReasons"] = _decode(payload.pop("rejection_reasons_json"), [])
    return payload


def _data_gaps(conn):
    fundamentals = conn.execute(
        "SELECT COUNT(*) FROM fundamental_observations"
    ).fetchone()[0]
    news = conn.execute("SELECT COUNT(*) FROM news_evidence").fetchone()[0]
    features = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               COUNT(pe) AS pe_rows, COUNT(pb) AS pb_rows,
               COUNT(revenue_yoy) AS revenue_rows, COUNT(eps_ttm) AS eps_rows
        FROM feature_snapshots
        """
    ).fetchone()
    return {
        "fundamentalObservations": int(fundamentals or 0),
        "newsEvidence": int(news or 0),
        "featureRows": int(features["rows"] or 0),
        "peFeatureRows": int(features["pe_rows"] or 0),
        "pbFeatureRows": int(features["pb_rows"] or 0),
        "revenueFeatureRows": int(features["revenue_rows"] or 0),
        "epsFeatureRows": int(features["eps_rows"] or 0),
    }


def _missed_opportunities(rows, limit=10):
    rejected = [
        row
        for row in rows
        if not bool(row.get("is_selected"))
        and _number(row.get("excess_return_3d")) is not None
        and float(row["excess_return_3d"]) > 0
    ]
    rejected.sort(key=lambda row: float(row["excess_return_3d"]), reverse=True)
    return [
        {
            "tradeDate": row.get("trade_date"),
            "code": row.get("code"),
            "name": row.get("name"),
            "industry": row.get("industry"),
            "score": _round(row.get("score"), 2),
            "netReturn3d": _round(row.get("net_return_3d")),
            "excessReturn3d": _round(row.get("excess_return_3d")),
            "selectionStatus": row.get("selection_status"),
        }
        for row in rejected[:limit]
    ]


def _separate_return_outliers(rows):
    valid = []
    outliers = []
    for row in rows:
        net = _number(row.get("net_return_3d"))
        excess = _number(row.get("excess_return_3d"))
        if (
            net is None
            or abs(net) > MAX_ABSOLUTE_T3_RETURN_PCT
            or (excess is not None and abs(excess) > MAX_ABSOLUTE_T3_EXCESS_PCT)
        ):
            outliers.append(
                {
                    "tradeDate": row.get("trade_date"),
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "netReturn3d": _round(net),
                    "excessReturn3d": _round(excess),
                    "reason": "implausible_t3_return",
                }
            )
        else:
            valid.append(row)
    return valid, outliers


def _diagnosis(forward, champion, selected, rejected):
    drawdown = _number(forward.get("max_drawdown_pct"))
    total_return = _number(forward.get("total_return_pct"))
    average_excess = _number(forward.get("avg_excess_return_pct"))
    closed = int(forward.get("closed_trades") or champion.get("closedTrades") or 0)
    if drawdown is not None and drawdown <= -12:
        return "hard_drawdown_stop"
    if drawdown is not None and drawdown <= -6 and (total_return or 0.0) <= 0:
        return "early_drawdown_breach"
    if closed < 30:
        return "prospective_evidence_thin"
    if (total_return or 0.0) <= 0 and (average_excess or 0.0) <= 0:
        return "prospective_edge_negative"
    selected_net = _number(selected.get("meanNetReturn"))
    rejected_net = _number(rejected.get("meanNetReturn"))
    if selected_net is not None and rejected_net is not None and selected_net <= rejected_net:
        return "selection_policy_not_adding_value"
    return "evidence_building"


def _find_attribution(attributions, dimension, bucket_key, scope="formal_selected"):
    return next(
        (
            row
            for row in attributions
            if row["scope"] == scope
            and row["dimension"] == dimension
            and row["bucketKey"] == bucket_key
        ),
        None,
    )


def propose_hypotheses(metrics, attributions):
    proposals = []
    forward = metrics["alphaForward"]
    challenger = metrics["modelChallenger"]
    data_gaps = metrics["dataGaps"]
    selected = metrics["recentSelected"]
    rejected = metrics["recentRejected"]

    def add(key, title, rationale, target_layer, priority, config, evidence):
        proposals.append(
            {
                "hypothesisKey": key,
                "title": title,
                "rationale": rationale,
                "targetLayer": target_layer,
                "priority": int(priority),
                "proposedConfig": config,
                "evidence": evidence,
            }
        )

    if data_gaps["fundamentalObservations"] == 0:
        add(
            "point_in_time_fundamentals_v1",
            "建立可回溯的基本面特徵層",
            "目前沒有任何 point-in-time 基本面觀測，模型無法驗證估值、營收與獲利品質是否能改善選股。",
            "data",
            100,
            {"features": ["pe", "pb", "revenue_yoy", "revenue_mom", "eps_ttm"]},
            data_gaps,
        )

    drawdown = _number(forward.get("max_drawdown_pct"))
    if drawdown is not None and drawdown <= -6:
        add(
            "alpha_forward_drawdown_control_v1",
            "重建 Alpha 前瞻回撤控制",
            "目前前瞻最大回撤已低於新版微型實盤容忍值，等待報酬翻正不能消除既有最大回撤。",
            "portfolio",
            95,
            {
                "max_position_weight": 0.04,
                "max_industry_weight": 0.12,
                "regime_abstention": True,
                "candidate_tail_risk_penalty": True,
            },
            {
                "maxDrawdownPct": drawdown,
                "totalReturnPct": forward.get("total_return_pct"),
                "closedTrades": forward.get("closed_trades"),
            },
        )

    challenger_net = _number(challenger.get("challenger_mean_net_return"))
    challenger_excess = _number(challenger.get("challenger_mean_excess_return"))
    challenger_trades = int(challenger.get("challenger_trades") or 0)
    if challenger_trades < 60 or (challenger_net or 0.0) <= 0 or (challenger_excess or 0.0) <= 0:
        add(
            "shadow_participation_calibration_v1",
            "重校準影子模型參與門檻",
            "目前影子模型入選樣本太少且樣本外成本後優勢為負，應只在訓練內校準區重新估計參與門檻與風險懲罰。",
            "model",
            90,
            {
                "calibration": "training_tail_only",
                "threshold_grid": [0.5, 0.6, 0.7, 0.8, 0.9],
                "objective": "after_cost_daily_excess_with_drawdown_penalty",
            },
            challenger,
        )

    selected_net = _number(selected.get("meanNetReturn"))
    rejected_net = _number(rejected.get("meanNetReturn"))
    if (
        int(selected.get("samples") or 0) >= 30
        and selected_net is not None
        and rejected_net is not None
        and selected_net <= rejected_net
    ):
        add(
            "candidate_selection_lift_rebuild_v1",
            "重建候選選擇增值",
            "近期正式入選沒有勝過落選對照組，應把學習目標改為同日橫斷面超額排名並允許空手。",
            "selection",
            88,
            {"target": "same_day_peer_excess_rank", "allow_cash": True, "top_k": 3},
            {"selected": selected, "rejected": rejected},
        )

    low_volume = _find_attribution(attributions, "volume", "lt_0_8")
    confirmed_volume = _find_attribution(attributions, "volume", "1_2_to_2")
    if (
        low_volume
        and confirmed_volume
        and low_volume["sampleCount"] >= 15
        and confirmed_volume["sampleCount"] >= 15
        and (_number(low_volume["meanNetReturn"]) or 0.0)
        < (_number(confirmed_volume["meanNetReturn"]) or 0.0)
    ):
        add(
            "moderate_volume_confirmation_v1",
            "驗證溫和量能確認",
            "近期 1.2x 至 2x 量能切片優於低於 0.8x 切片，應以鎖定門檻做 challenger 消融。",
            "selection",
            75,
            {"min_volume_ratio_5": 1.2, "max_volume_ratio_5": 3.5},
            {"lowVolume": low_volume, "confirmedVolume": confirmed_volume},
        )

    risk_off = _find_attribution(attributions, "market_regime", "risk_off")
    supportive = _find_attribution(attributions, "market_regime", "supportive")
    if (
        risk_off
        and supportive
        and risk_off["sampleCount"] >= 15
        and supportive["sampleCount"] >= 15
        and (_number(risk_off["meanNetReturn"]) or 0.0)
        < (_number(supportive["meanNetReturn"]) or 0.0)
    ):
        add(
            "market_regime_abstention_v1",
            "驗證風險收縮時空手",
            "風險收縮切片的成本後結果弱於多方廣度切片，應驗證 regime abstention，而不是在弱市硬選三檔。",
            "portfolio",
            78,
            {"risk_off_action": "cash", "minimum_market_up_ratio": 45},
            {"riskOff": risk_off, "supportive": supportive},
        )

    return sorted(proposals, key=lambda row: (-row["priority"], row["hypothesisKey"]))


def _upsert_hypotheses(conn, cycle_id, generated_at, proposals):
    for proposal in proposals:
        conn.execute(
            """
            INSERT INTO learning_hypotheses (
                hypothesis_key, title, rationale, target_layer, status, priority,
                first_cycle_id, latest_cycle_id, occurrences,
                proposed_config_json, evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(hypothesis_key) DO UPDATE SET
                title=excluded.title,
                rationale=excluded.rationale,
                target_layer=excluded.target_layer,
                priority=excluded.priority,
                latest_cycle_id=excluded.latest_cycle_id,
                occurrences=CASE
                    WHEN learning_hypotheses.latest_cycle_id <> excluded.latest_cycle_id
                    THEN learning_hypotheses.occurrences + 1
                    ELSE learning_hypotheses.occurrences
                END,
                proposed_config_json=excluded.proposed_config_json,
                evidence_json=excluded.evidence_json,
                updated_at=excluded.updated_at
            """,
            (
                proposal["hypothesisKey"],
                proposal["title"],
                proposal["rationale"],
                proposal["targetLayer"],
                proposal["priority"],
                cycle_id,
                cycle_id,
                json.dumps(proposal["proposedConfig"], ensure_ascii=False, sort_keys=True),
                json.dumps(proposal["evidence"], ensure_ascii=False, sort_keys=True),
                generated_at,
                generated_at,
            ),
        )


def _status(diagnosis):
    if diagnosis == "hard_drawdown_stop":
        return "paused"
    if diagnosis in {"early_drawdown_breach", "prospective_edge_negative", "selection_policy_not_adding_value"}:
        return "redesign_required"
    if diagnosis == "prospective_evidence_thin":
        return "collecting"
    return "evidence_review"


def _fmt_pct(value):
    number = _number(value)
    return "--" if number is None else f"{number:+.2f}%"


def _render_report(cycle_date, status, diagnosis, metrics, attributions, proposals):
    forward = metrics["alphaForward"]
    selected = metrics["recentSelected"]
    rejected = metrics["recentRejected"]
    account_lines = [
        f"| {row['name']} | {row['closedTrades']} | {_fmt_pct(row['totalReturnPct'])} | {_fmt_pct(row['meanExcessReturn'])} | {_fmt_pct(row['maxDrawdownPct'])} |"
        for row in metrics["paperAccounts"]
    ]
    negative_slices = [
        row
        for row in attributions
        if row["scope"] == "formal_selected"
        and row["sampleCount"] >= 10
        and (_number(row["meanNetReturn"]) or 0.0) < 0
    ]
    negative_slices.sort(
        key=lambda row: (
            _number(row["meanNetReturn"]) or 0.0,
            -row["sampleCount"],
        )
    )
    attribution_lines = [
        f"| {row['dimensionLabel']} | {row['bucketLabel']} | {row['sampleCount']} | {_fmt_pct(row['meanNetReturn'])} | {_fmt_pct(row['meanExcessReturn'])} | {_fmt_pct(row['meanDrawdown'])} |"
        for row in negative_slices[:12]
    ]
    hypothesis_lines = [
        f"{index}. **{row['title']}** (`{row['targetLayer']}`, priority {row['priority']})：{row['rationale']}"
        for index, row in enumerate(proposals, start=1)
    ]
    missed_lines = [
        f"- {row['tradeDate']} {row['code']} {row['name']}：T+3 淨報酬 {_fmt_pct(row['netReturn3d'])}，超額 {_fmt_pct(row['excessReturn3d'])}，原狀態 `{row['selectionStatus']}`。"
        for row in metrics["missedOpportunities"][:8]
    ]
    return "\n".join(
        [
            f"# 自動研究週期：{cycle_date}",
            "",
            f"- 版本：`{LEARNING_CYCLE_VERSION}`",
            f"- 狀態：`{status}`",
            f"- 主要診斷：`{diagnosis}`",
            f"- 證據期間：{metrics['evidenceStartDate'] or '--'} 至 {metrics['evidenceEndDate'] or '--'}",
            f"- 品質排除：{len(metrics['qualityExclusions'])} 筆異常 T+3 標籤未進入歸因與假設生成。",
            "- 治理限制：本報告只提出研究假設，不會修改正式策略、權重或下單設定。",
            "",
            "## 前瞻 Alpha",
            "",
            f"- 決策日：{int(forward.get('decision_days') or 0)}",
            f"- 結案交易：{int(forward.get('closed_trades') or 0)}",
            f"- 成本後總報酬：{_fmt_pct(forward.get('total_return_pct'))}",
            f"- 平均超額報酬：{_fmt_pct(forward.get('avg_excess_return_pct'))}",
            f"- 最大回撤：{_fmt_pct(forward.get('max_drawdown_pct'))}",
            f"- PSR：{_number(forward.get('probabilistic_sharpe')) or 0.0:.3f}",
            "",
            "## 模擬帳戶對照",
            "",
            "| 帳戶 | 結案 | 總報酬 | 平均超額 | 最大回撤 |",
            "| --- | ---: | ---: | ---: | ---: |",
            *(account_lines or ["| 尚無資料 | 0 | -- | -- | -- |"]),
            "",
            "帳戶總報酬包含持有部位的市值變化；平均超額只計算已結案交易，兩者不可直接互換解讀。",
            "",
            "## 近期成熟候選",
            "",
            f"- 正式入選：{selected['samples']} 筆，平均淨報酬 {_fmt_pct(selected['meanNetReturn'])}，平均超額 {_fmt_pct(selected['meanExcessReturn'])}。",
            f"- 落選對照：{rejected['samples']} 筆，平均淨報酬 {_fmt_pct(rejected['meanNetReturn'])}，平均超額 {_fmt_pct(rejected['meanExcessReturn'])}。",
            "",
            "## 主要負向切片",
            "",
            "| 維度 | 切片 | 樣本 | 平均淨報酬 | 平均超額 | 平均回撤 |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
            *(attribution_lines or ["| 尚無足量負向切片 | -- | 0 | -- | -- | -- |"]),
            "",
            "## 漏選機會",
            "",
            *(missed_lines or ["- 近期沒有已成熟且超額為正的落選候選。"]),
            "",
            "## 下一輪研究假設",
            "",
            *(hypothesis_lines or ["1. 目前沒有達到最低證據要求的新假設。"]),
            "",
            "## 決策",
            "",
            "維持影子研究。任何假設必須先固定設定、完成 purged walk-forward、保留區間與全新前瞻模擬，才可進入人工升級審查。",
            "",
        ]
    )


def run_learning_cycle(
    db_path=DB_PATH,
    as_of=None,
    report_dir=DEFAULT_REPORT_DIR,
    recent_trade_dates=RECENT_TRADE_DATES,
):
    generated_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        cycle_date = _latest_trade_date(conn, as_of=as_of)
        if cycle_date is None:
            return {"status": "skipped", "reason": "no_scan_runs"}
        evidence_start, evidence_end = _recent_window(
            conn, cycle_date, limit=recent_trade_dates
        )
        raw_rows = _candidate_outcomes(conn, evidence_start, evidence_end)
        rows, quality_exclusions = _separate_return_outliers(raw_rows)
        attributions = build_failure_attributions(rows)
        selected = _scope_summary(rows, True)
        rejected = _scope_summary(rows, False)
        accounts = _paper_accounts(conn)
        champion = next(
            (row for row in accounts if row["accountKey"] == PRIMARY_ACCOUNT_KEY), {}
        )
        forward = _latest_json_snapshot(conn, "alpha_forward_snapshots", "evaluated_at")
        challenger = _latest_model_challenger(conn)
        data_gaps = _data_gaps(conn)
        missed = _missed_opportunities(rows)
        previous = conn.execute(
            """
            SELECT generated_at FROM research_cycles
            WHERE cycle_version=? AND cycle_date < ?
            ORDER BY cycle_date DESC, generated_at DESC LIMIT 1
            """,
            (LEARNING_CYCLE_VERSION, cycle_date),
        ).fetchone()
        new_matured = conn.execute(
            """
            SELECT COUNT(*) FROM candidate_outcomes co
            JOIN candidate_events ce ON ce.id=co.candidate_id
            JOIN scan_runs sr ON sr.id=ce.run_id
            WHERE co.execution_version=? AND co.matured_horizon >= 3
              AND sr.trade_date <= ?
              AND (? IS NULL OR co.evaluated_at > ?)
            """,
            (
                CANDIDATE_EXECUTION_VERSION,
                cycle_date,
                previous[0] if previous else None,
                previous[0] if previous else None,
            ),
        ).fetchone()[0]
        metrics = {
            "evidenceStartDate": evidence_start,
            "evidenceEndDate": evidence_end,
            "recentTradeDates": int(recent_trade_dates),
            "recentSelected": selected,
            "recentRejected": rejected,
            "selectionNetLift": _round(
                (_number(selected["meanNetReturn"]) or 0.0)
                - (_number(rejected["meanNetReturn"]) or 0.0)
            )
            if selected["meanNetReturn"] is not None and rejected["meanNetReturn"] is not None
            else None,
            "selectionExcessLift": _round(
                (_number(selected["meanExcessReturn"]) or 0.0)
                - (_number(rejected["meanExcessReturn"]) or 0.0)
            )
            if selected["meanExcessReturn"] is not None and rejected["meanExcessReturn"] is not None
            else None,
            "alphaForward": forward,
            "paperAccounts": accounts,
            "modelChallenger": challenger,
            "dataGaps": data_gaps,
            "missedOpportunities": missed,
            "qualityExclusions": quality_exclusions,
        }
        diagnosis = _diagnosis(forward, champion, selected, rejected)
        cycle_status = _status(diagnosis)
        proposals = propose_hypotheses(metrics, attributions)
        report_path = Path(report_dir) / f"research_cycle_{cycle_date}.md"
        report = _render_report(
            cycle_date, cycle_status, diagnosis, metrics, attributions, proposals
        )
        conn.execute(
            """
            INSERT INTO research_cycles (
                cycle_date, generated_at, cycle_version, status,
                evidence_start_date, evidence_end_date, primary_diagnosis,
                new_matured_outcomes, closed_champion_trades, metrics_json,
                report_path, report_markdown, git_commit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cycle_date, cycle_version) DO UPDATE SET
                generated_at=excluded.generated_at,
                status=excluded.status,
                evidence_start_date=excluded.evidence_start_date,
                evidence_end_date=excluded.evidence_end_date,
                primary_diagnosis=excluded.primary_diagnosis,
                new_matured_outcomes=excluded.new_matured_outcomes,
                closed_champion_trades=excluded.closed_champion_trades,
                metrics_json=excluded.metrics_json,
                report_path=excluded.report_path,
                report_markdown=excluded.report_markdown,
                git_commit=excluded.git_commit
            """,
            (
                cycle_date,
                generated_at,
                LEARNING_CYCLE_VERSION,
                cycle_status,
                evidence_start,
                evidence_end,
                diagnosis,
                int(new_matured or 0),
                int(champion.get("closedTrades") or 0),
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                str(report_path),
                report,
                get_git_commit(),
            ),
        )
        cycle_id = conn.execute(
            "SELECT id FROM research_cycles WHERE cycle_date=? AND cycle_version=?",
            (cycle_date, LEARNING_CYCLE_VERSION),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM research_failure_attributions WHERE cycle_id=?",
            (cycle_id,),
        )
        for record in attributions:
            conn.execute(
                """
                INSERT INTO research_failure_attributions (
                    cycle_id, scope, dimension, bucket_key, bucket_label,
                    sample_count, mean_net_return, mean_excess_return,
                    positive_rate, mean_drawdown, standard_error,
                    ci95_low, ci95_high, loss_contribution, evidence_json,
                    sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    record["scope"],
                    record["dimension"],
                    record["bucketKey"],
                    record["bucketLabel"],
                    record["sampleCount"],
                    record["meanNetReturn"],
                    record["meanExcessReturn"],
                    record["positiveRate"],
                    record["meanDrawdown"],
                    record["standardError"],
                    record["ci95Low"],
                    record["ci95High"],
                    record["lossContribution"],
                    json.dumps(
                        {"codes": record["codes"]}, ensure_ascii=False, sort_keys=True
                    ),
                    record["sortOrder"],
                ),
            )
        _upsert_hypotheses(conn, cycle_id, generated_at, proposals)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return {
        "status": cycle_status,
        "cycleDate": cycle_date,
        "cycleVersion": LEARNING_CYCLE_VERSION,
        "primaryDiagnosis": diagnosis,
        "evidenceStartDate": evidence_start,
        "evidenceEndDate": evidence_end,
        "candidateOutcomes": len(rows),
        "qualityExclusions": len(quality_exclusions),
        "attributions": len(attributions),
        "hypotheses": len(proposals),
        "reportPath": str(report_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the governed daily learning cycle and research journal."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--as-of")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--recent-trade-dates", type=int, default=RECENT_TRADE_DATES)
    args = parser.parse_args()
    result = run_learning_cycle(
        db_path=args.db,
        as_of=args.as_of,
        report_dir=args.report_dir,
        recent_trade_dates=args.recent_trade_dates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
