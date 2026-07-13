import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path

import pandas as pd

from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)
from llm_agent import analyze_news_evidence_claude, fetch_google_news_evidence


FEATURE_VERSION = "candidate_features_v1"
MODEL_NAME = "shadow_logistic_t3"
MODEL_FAMILY_VERSION = "shadow_logistic_t3_v1"
MIN_TRAINING_SAMPLES = 80
MIN_POSITIVE_SAMPLES = 10
VALIDATION_FRACTION = 0.20
EMBARGO_TRADE_DATES = 3
MAX_NEWS_CANDIDATES = 3
MIN_SHADOW_PROBABILITY = 0.35
MIN_SHADOW_EXPECTED_EXCESS = 0.0
MIN_SHADOW_EXPECTED_DRAWDOWN = -4.0

MODEL_FEATURES = [
    "candidate_score",
    "strategy_count",
    "strategy_trend",
    "strategy_reversal",
    "strategy_wave",
    "pct_change",
    "turnover_billion",
    "volume_ratio_5",
    "volume_ratio_20",
    "intraday_position",
    "rsi",
    "industry_up_ratio",
    "industry_avg_return",
    "industry_heat",
    "stop_distance_pct",
]


def _safe_float(value):
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "")
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _safe_int(value):
    number = _safe_float(value)
    return None if number is None else int(number)


def _decode_json(value, default):
    try:
        decoded = json.loads(value or "")
        return decoded if isinstance(decoded, type(default)) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _bounded_sigmoid(value, scale=1.0):
    value = max(-30.0, min(30.0, float(value) / scale))
    return 1.0 / (1.0 + math.exp(-value))


def candidate_to_feature(event):
    snapshot = _decode_json(event.get("snapshot_json"), {})
    strategies = {
        str(value).strip().lower()
        for value in _decode_json(event.get("strategies_json"), [])
    }
    strategy_text = str(snapshot.get("策略", ""))
    if "順勢" in strategy_text:
        strategies.add("trend")
    if "低檔" in strategy_text or "抄底" in strategy_text:
        strategies.add("reversal")
    if "波段" in strategy_text:
        strategies.add("wave")

    feature_values = {
        "candidate_score": _safe_float(event.get("score")),
        "strategy_count": _safe_int(event.get("strategy_count")) or len(strategies),
        "strategy_trend": int("trend" in strategies),
        "strategy_reversal": int("reversal" in strategies),
        "strategy_wave": int("wave" in strategies),
        "tradable": int(bool(event.get("tradable"))),
        "is_first_eligible_event": int(bool(event.get("is_first_eligible_event"))),
        "stop_distance_pct": _safe_float(event.get("stop_distance_pct")),
        "price": _safe_float(event.get("signal_price")),
        "pct_change": _safe_float(event.get("pct_change")),
        "turnover_billion": _safe_float(event.get("turnover_billion")),
        "volume_ratio_5": _safe_float(event.get("volume_ratio_5")),
        "volume_ratio_20": _safe_float(snapshot.get("量比20")),
        "intraday_position": _safe_float(event.get("intraday_position")),
        "rsi": _safe_float(snapshot.get("RSI")),
        "industry_up_ratio": _safe_float(snapshot.get("產業上漲比例")),
        "industry_avg_return": _safe_float(snapshot.get("產業平均漲跌幅")),
        "industry_heat": _safe_float(snapshot.get("產業熱度分數")),
        "market_up_ratio": _safe_float(snapshot.get("市場上漲比例")),
        "market_avg_return": _safe_float(snapshot.get("市場平均漲跌幅")),
        "market_median_return": _safe_float(snapshot.get("市場中位數漲跌幅")),
    }
    return {
        "run_id": int(event["run_id"]),
        "signal_id": event.get("signal_id"),
        "code": str(event["code"]),
        "as_of": event.get("as_of") or event.get("run_at"),
        "feature_version": FEATURE_VERSION,
        **feature_values,
        "features_json": json.dumps(
            {
                "industry": event.get("industry"),
                "strategies": sorted(strategies),
                "policy_version": event.get("policy_version"),
                "selection_status": event.get("selection_status"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def build_feature_snapshots(db_path=DB_PATH, run_id=None, missing_only=True):
    where = []
    params = []
    join_sql = (
        "LEFT JOIN feature_snapshots fs "
        "ON fs.run_id=ce.run_id AND fs.code=ce.code AND fs.feature_version=?"
        if missing_only
        else ""
    )
    if missing_only:
        params.append(FEATURE_VERSION)
        where.append("fs.id IS NULL")
    if run_id is not None:
        params.append(int(run_id))
        where.append("ce.run_id = ?")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with get_connection(db_path) as conn:
        init_db(conn)
        rows = conn.execute(
            f"""
            SELECT ce.*, sr.run_at
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            {join_sql}
            {where_sql}
            ORDER BY ce.id
            """,
            tuple(params),
        ).fetchall()
        records = [candidate_to_feature(dict(row)) for row in rows]
        columns = [
            "run_id", "signal_id", "code", "as_of", "feature_version",
            "candidate_score", "strategy_count", "strategy_trend",
            "strategy_reversal", "strategy_wave", "tradable",
            "is_first_eligible_event", "stop_distance_pct", "price",
            "pct_change", "turnover_billion", "volume_ratio_5",
            "volume_ratio_20", "intraday_position", "rsi",
            "industry_up_ratio", "industry_avg_return", "industry_heat",
            "market_up_ratio", "market_avg_return", "market_median_return",
            "features_json", "created_at",
        ]
        now = get_taipei_now().isoformat(timespec="seconds")
        for record in records:
            record["created_at"] = now
            placeholders = ", ".join("?" for _ in columns)
            updates = ", ".join(
                f"{column}=excluded.{column}"
                for column in columns
                if column not in {"run_id", "code", "feature_version", "created_at"}
            )
            conn.execute(
                f"""
                INSERT INTO feature_snapshots ({', '.join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(run_id, code, feature_version) DO UPDATE SET {updates}
                """,
                tuple(record.get(column) for column in columns),
            )
        conn.commit()
    return len(records)


def _load_candidate_training_frame(conn):
    columns = ", ".join(f"fs.{name}" for name in MODEL_FEATURES)
    return pd.read_sql_query(
        f"""
        WITH canonical AS (
            SELECT ce.id AS candidate_id, ce.run_id, ce.code, ce.as_of,
                   sr.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.trade_date, ce.code
                       ORDER BY ce.as_of ASC, ce.id ASC
                   ) AS day_code_rank
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
        )
        SELECT fs.id AS feature_id, fs.run_id, fs.signal_id, fs.code, fs.as_of,
               canonical.trade_date, {columns}, co.success_t3,
               co.excess_return_3d, co.max_drawdown_3d
        FROM canonical
        JOIN candidate_outcomes co ON co.candidate_id=canonical.candidate_id
        JOIN feature_snapshots fs
          ON fs.run_id=canonical.run_id AND fs.code=canonical.code
        WHERE canonical.day_code_rank=1
          AND fs.feature_version=?
          AND co.execution_version=?
          AND co.matured_horizon >= 3
          AND co.success_t3 IS NOT NULL
          AND co.excess_return_3d IS NOT NULL
          AND co.max_drawdown_3d IS NOT NULL
        ORDER BY canonical.trade_date, fs.as_of, fs.id
        """,
        conn,
        params=(FEATURE_VERSION, CANDIDATE_EXECUTION_VERSION),
    )


def _load_legacy_training_frame(conn):
    columns = ", ".join(f"fs.{name}" for name in MODEL_FEATURES)
    return pd.read_sql_query(
        f"""
        SELECT fs.id AS feature_id, fs.run_id, fs.signal_id, fs.code, fs.as_of,
               sr.trade_date, {columns}, br.success_t3,
               br.excess_return_3d, br.max_drawdown_3d
        FROM feature_snapshots fs
        JOIN scan_runs sr ON sr.id=fs.run_id
        JOIN backtest_results br ON br.signal_id=fs.signal_id
        WHERE fs.feature_version=?
          AND br.matured_horizon >= 3
          AND br.success_t3 IS NOT NULL
          AND br.excess_return_3d IS NOT NULL
          AND br.max_drawdown_3d IS NOT NULL
        ORDER BY sr.trade_date, fs.as_of, fs.id
        """,
        conn,
        params=(FEATURE_VERSION,),
    )


def _load_training_frame(
    conn,
    min_candidate_samples=MIN_TRAINING_SAMPLES,
    min_candidate_positives=MIN_POSITIVE_SAMPLES,
):
    candidate_frame = _load_candidate_training_frame(conn)
    positives = int(candidate_frame["success_t3"].sum()) if not candidate_frame.empty else 0
    negatives = len(candidate_frame) - positives
    if (
        len(candidate_frame) >= min_candidate_samples
        and positives >= min_candidate_positives
        and negatives >= min_candidate_positives
    ):
        candidate_frame.attrs["outcome_source"] = CANDIDATE_EXECUTION_VERSION
        return candidate_frame
    legacy = _load_legacy_training_frame(conn)
    legacy.attrs["outcome_source"] = "legacy_signal_backtest"
    return legacy


def _purged_date_split(
    frame,
    validation_fraction=VALIDATION_FRACTION,
    embargo_trade_dates=EMBARGO_TRADE_DATES,
):
    dates = sorted(str(value) for value in frame["trade_date"].dropna().unique())
    if len(dates) < 3:
        return frame.iloc[0:0].copy(), frame.iloc[0:0].copy(), []

    validation_date_count = max(1, int(math.ceil(len(dates) * validation_fraction)))
    validation_start = len(dates) - validation_date_count
    train_end = max(0, validation_start - int(embargo_trade_dates))
    training_dates = set(dates[:train_end])
    embargo_dates = dates[train_end:validation_start]
    validation_dates = set(dates[validation_start:])
    train = frame[frame["trade_date"].astype(str).isin(training_dates)].copy()
    validation = frame[frame["trade_date"].astype(str).isin(validation_dates)].copy()
    return train, validation, embargo_dates


def train_shadow_model(
    db_path=DB_PATH,
    artifact_dir="data/models",
    min_samples=MIN_TRAINING_SAMPLES,
    min_positives=MIN_POSITIVE_SAMPLES,
):
    try:
        import joblib
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.metrics import brier_score_loss, mean_absolute_error, roc_auc_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        return {"status": "dependency_missing", "message": str(exc), "model": None}

    with get_connection(db_path) as conn:
        init_db(conn)
        frame = _load_training_frame(
            conn,
            min_candidate_samples=min_samples,
            min_candidate_positives=min_positives,
        )
    outcome_source = frame.attrs.get("outcome_source", "unknown")

    positives = int(frame["success_t3"].sum()) if not frame.empty else 0
    if len(frame) < min_samples or positives < min_positives or len(frame) - positives < min_positives:
        return {
            "status": "insufficient_data",
            "samples": len(frame),
            "positives": positives,
            "message": f"需要至少 {min_samples} 筆且正負樣本各 {min_positives} 筆",
            "model": None,
        }

    minimum_training_size = max(10, min_samples // 2)
    train, validation, embargo_dates = _purged_date_split(frame)
    if len(train) < minimum_training_size or validation.empty:
        return {
            "status": "insufficient_time_split",
            "samples": len(frame),
            "positives": positives,
            "message": "日期分組與隔離區套用後，訓練或驗證樣本不足",
            "model": None,
        }
    if train["success_t3"].nunique() < 2:
        return {
            "status": "insufficient_training_classes",
            "samples": len(frame),
            "positives": positives,
            "message": "時序訓練區間尚未同時包含成功與失敗樣本",
            "model": None,
        }

    classifier = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=42,
                    C=0.5,
                ),
            ),
        ]
    )
    regression = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=5.0)),
        ]
    )
    drawdown = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=5.0)),
        ]
    )

    classifier.fit(train[MODEL_FEATURES], train["success_t3"].astype(int))
    regression.fit(train[MODEL_FEATURES], train["excess_return_3d"])
    drawdown.fit(train[MODEL_FEATURES], train["max_drawdown_3d"])
    probabilities = classifier.predict_proba(validation[MODEL_FEATURES])[:, 1]
    excess_predictions = regression.predict(validation[MODEL_FEATURES])
    drawdown_predictions = drawdown.predict(validation[MODEL_FEATURES])
    auc = None
    if validation["success_t3"].nunique() == 2:
        auc = float(roc_auc_score(validation["success_t3"], probabilities))
    metrics = {
        "samples": int(len(frame)),
        "positive_samples": positives,
        "training_samples": int(len(train)),
        "validation_samples": int(len(validation)),
        "unique_trade_dates": int(frame["trade_date"].nunique()),
        "embargo_trade_dates": list(embargo_dates),
        "outcome_source": outcome_source,
        "validation_auc": auc,
        "validation_brier": float(brier_score_loss(validation["success_t3"], probabilities)),
        "validation_excess_mae": float(mean_absolute_error(validation["excess_return_3d"], excess_predictions)),
        "validation_drawdown_mae": float(mean_absolute_error(validation["max_drawdown_3d"], drawdown_predictions)),
        "validation_start": str(validation["trade_date"].min()),
        "validation_end": str(validation["trade_date"].max()),
    }
    version = (
        f"{MODEL_FAMILY_VERSION}-{frame['trade_date'].max().replace('-', '')}"
        f"-n{len(frame)}"
    )
    artifact_path = Path(artifact_dir) / f"{version}.joblib"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "classifier": classifier,
        "excess_regression": regression,
        "drawdown_regression": drawdown,
        "features": MODEL_FEATURES,
        "feature_version": FEATURE_VERSION,
        "model_version": version,
        "metrics": metrics,
    }
    joblib.dump(bundle, artifact_path)

    now = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO model_versions (
                model_name, version, status, feature_version, training_start,
                training_end, config_json, metrics_json, artifact_path, created_at
            ) VALUES (?, ?, 'shadow', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_name, version) DO UPDATE SET
                status=excluded.status, config_json=excluded.config_json,
                metrics_json=excluded.metrics_json, artifact_path=excluded.artifact_path
            """,
            (
                MODEL_NAME,
                version,
                FEATURE_VERSION,
                str(train["trade_date"].min()),
                str(train["trade_date"].max()),
                json.dumps(
                    {
                        "features": MODEL_FEATURES,
                        "classifier": "balanced_logistic_regression",
                        "regression": "ridge",
                        "time_split": "purged_trade_date_groups",
                        "embargo_trade_dates": EMBARGO_TRADE_DATES,
                        "outcome_source": outcome_source,
                        "git_commit": get_git_commit(),
                    },
                    sort_keys=True,
                ),
                json.dumps(metrics, sort_keys=True),
                str(artifact_path),
                now,
            ),
        )
        conn.commit()
    return {
        "status": "trained",
        "version": version,
        "metrics": metrics,
        "artifact_path": str(artifact_path),
        "model": bundle,
    }


def latest_candidate_run_id(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT ce.run_id
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            ORDER BY sr.run_at DESC, ce.run_id DESC
            LIMIT 1
            """
        ).fetchone()
    return int(row[0]) if row else None


def save_shadow_predictions(run_id, training_result, db_path=DB_PATH):
    bundle = training_result.get("model")
    if not bundle:
        return []
    with get_connection(db_path) as conn:
        init_db(conn)
        frame = pd.read_sql_query(
            f"""
            SELECT fs.*, ce.name, ce.industry, ce.observation_price,
                   ce.chase_limit, ce.policy_version, sr.trade_date
            FROM feature_snapshots fs
            JOIN candidate_events ce
              ON ce.run_id=fs.run_id AND ce.code=fs.code
             AND ce.policy_version=(
                SELECT ce2.policy_version FROM candidate_events ce2
                WHERE ce2.run_id=ce.run_id AND ce2.code=ce.code
                ORDER BY ce2.id DESC LIMIT 1
             )
            JOIN scan_runs sr ON sr.id=fs.run_id
            WHERE fs.run_id=? AND fs.feature_version=?
            ORDER BY ce.raw_rank, fs.id
            """,
            conn,
            params=(int(run_id), FEATURE_VERSION),
        )
    if frame.empty:
        return []

    probability = bundle["classifier"].predict_proba(frame[MODEL_FEATURES])[:, 1]
    expected_excess = bundle["excess_regression"].predict(frame[MODEL_FEATURES])
    expected_drawdown = bundle["drawdown_regression"].predict(frame[MODEL_FEATURES])
    frame["probability_t3"] = probability
    frame["expected_excess_return_3d"] = expected_excess
    frame["expected_max_drawdown_3d"] = expected_drawdown
    frame["final_score"] = [
        100.0
        * (
            0.70 * p
            + 0.20 * _bounded_sigmoid(excess, scale=3.0)
            + 0.10 * _bounded_sigmoid(drawdown + 4.0, scale=2.0)
        )
        for p, excess, drawdown in zip(probability, expected_excess, expected_drawdown)
    ]
    eligible = frame[
        (frame["tradable"].fillna(0).astype(int) == 1)
        & (frame["is_first_eligible_event"].fillna(0).astype(int) == 1)
        & (frame["probability_t3"] >= MIN_SHADOW_PROBABILITY)
        & (frame["expected_excess_return_3d"] >= MIN_SHADOW_EXPECTED_EXCESS)
        & (frame["expected_max_drawdown_3d"] >= MIN_SHADOW_EXPECTED_DRAWDOWN)
    ].sort_values("final_score", ascending=False)
    shadow_selected = []
    industries = set()
    for index, row in eligible.iterrows():
        industry = str(row.get("industry") or "")
        if industry and industry in industries:
            continue
        shadow_selected.append(index)
        if industry:
            industries.add(industry)
        if len(shadow_selected) >= 3:
            break

    now = get_taipei_now().isoformat(timespec="seconds")
    saved = []
    with get_connection(db_path) as conn:
        init_db(conn)
        ranked_frame = frame.assign(
            _eligible_rank=(
                (frame["tradable"].fillna(0).astype(int) == 1)
                & (frame["is_first_eligible_event"].fillna(0).astype(int) == 1)
            ).astype(int)
        ).sort_values(["_eligible_rank", "final_score"], ascending=[False, False])
        for rank, (index, row) in enumerate(ranked_frame.iterrows(), start=1):
            is_selected = index in shadow_selected
            is_prospective = str(row.get("trade_date") or "") == now[:10]
            probability_value = float(row["probability_t3"])
            if not bool(row.get("tradable")):
                action = "blocked_by_risk_policy"
            elif is_selected:
                action = "shadow_watch"
            elif probability_value < 0.25:
                action = "shadow_avoid"
            else:
                action = "shadow_neutral"
            rationale = {
                    "shadow_mode": True,
                    "prospective": is_prospective,
                "rule_policy_unchanged": True,
                "shadow_thresholds": {
                    "minimum_probability_t3": MIN_SHADOW_PROBABILITY,
                    "minimum_expected_excess_return_3d": MIN_SHADOW_EXPECTED_EXCESS,
                    "minimum_expected_max_drawdown_3d": MIN_SHADOW_EXPECTED_DRAWDOWN,
                },
                "feature_version": FEATURE_VERSION,
                "model_metrics": training_result.get("metrics", {}),
                "score_components": {
                    "probability_t3": probability_value,
                    "expected_excess_return_3d": float(row["expected_excess_return_3d"]),
                    "expected_max_drawdown_3d": float(row["expected_max_drawdown_3d"]),
                },
            }
            entry_price = _safe_float(row.get("price"))
            chase_limit = _safe_float(row.get("chase_limit"))
            stop_price = _safe_float(row.get("observation_price"))
            cursor = conn.execute(
                """
                INSERT INTO predictions (
                    run_id, signal_id, code, predicted_at, model_version,
                    is_prospective, rank_order, is_selected, final_score, probability_t3,
                    expected_excess_return_3d, expected_max_drawdown_3d,
                    action, entry_low, entry_high, chase_limit, stop_price,
                    rationale_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, code, model_version) DO UPDATE SET
                    signal_id=excluded.signal_id, predicted_at=excluded.predicted_at,
                    is_prospective=excluded.is_prospective,
                    rank_order=excluded.rank_order, is_selected=excluded.is_selected,
                    final_score=excluded.final_score,
                    probability_t3=excluded.probability_t3,
                    expected_excess_return_3d=excluded.expected_excess_return_3d,
                    expected_max_drawdown_3d=excluded.expected_max_drawdown_3d,
                    action=excluded.action, entry_low=excluded.entry_low,
                    entry_high=excluded.entry_high, chase_limit=excluded.chase_limit,
                    stop_price=excluded.stop_price,
                    rationale_json=excluded.rationale_json
                """,
                (
                    int(run_id),
                    _safe_int(row.get("signal_id")),
                    str(row["code"]),
                    now,
                    training_result["version"],
                    int(is_prospective),
                    rank,
                    int(is_selected),
                    float(row["final_score"]),
                    probability_value,
                    float(row["expected_excess_return_3d"]),
                    float(row["expected_max_drawdown_3d"]),
                    action,
                    entry_price,
                    min(entry_price, chase_limit) if entry_price is not None and chase_limit is not None else entry_price,
                    chase_limit,
                    stop_price,
                    json.dumps(rationale, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            saved.append(
                {
                    "id": cursor.lastrowid,
                    "code": str(row["code"]),
                    "name": str(row.get("name") or ""),
                    "industry": str(row.get("industry") or ""),
                    "rank": rank,
                    "is_prospective": is_prospective,
                    "is_selected": is_selected,
                    "probability_t3": probability_value,
                    "expected_excess_return_3d": float(row["expected_excess_return_3d"]),
                    "expected_max_drawdown_3d": float(row["expected_max_drawdown_3d"]),
                    "action": action,
                }
            )
        conn.commit()
    return saved


def update_prediction_outcomes(db_path=DB_PATH):
    now = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        candidate_rows = conn.execute(
            """
            WITH canonical_candidate AS (
                SELECT id, run_id, code
                FROM (
                    SELECT ce.id, ce.run_id, ce.code,
                           ROW_NUMBER() OVER (
                               PARTITION BY ce.run_id, ce.code
                               ORDER BY ce.id DESC
                           ) AS candidate_order
                    FROM candidate_events ce
                )
                WHERE candidate_order=1
            )
            SELECT
                p.id AS prediction_id,
                co.entry_at AS entry_date,
                co.entry_price,
                co.entry_method,
                co.fixed_net_return_1d AS net_return_1d,
                co.net_return_3d,
                co.fixed_net_return_5d AS net_return_5d,
                co.benchmark_return_3d,
                co.excess_return_3d,
                co.max_return_3d,
                co.max_drawdown_3d,
                CASE WHEN co.defense_triggered=1 THEN co.exit_at END AS stop_loss_date,
                co.defense_triggered AS stop_loss_hit,
                co.success_t3,
                co.matured_horizon,
                co.outcome_status,
                co.evaluated_at AS tested_at
            FROM predictions p
            JOIN canonical_candidate ce
              ON ce.run_id=p.run_id AND ce.code=p.code
            JOIN candidate_outcomes co
              ON co.candidate_id=ce.id AND co.execution_version=?
            WHERE p.is_prospective=1
            """,
            (CANDIDATE_EXECUTION_VERSION,),
        ).fetchall()
        candidate_prediction_ids = {
            int(row["prediction_id"]) for row in candidate_rows
        }
        legacy_rows = conn.execute(
            """
            SELECT p.id AS prediction_id, br.*
            FROM predictions p
            JOIN backtest_results br ON br.signal_id=p.signal_id
            WHERE p.is_prospective=1
            """
        ).fetchall()
        rows = list(candidate_rows) + [
            row
            for row in legacy_rows
            if int(row["prediction_id"]) not in candidate_prediction_ids
        ]
        conn.execute(
            """
            DELETE FROM prediction_outcomes
            WHERE prediction_id IN (
                SELECT id FROM predictions WHERE is_prospective=0
            )
            """
        )
        for row in rows:
            row = dict(row)
            conn.execute(
                """
                INSERT INTO prediction_outcomes (
                    prediction_id, entry_at, entry_price, entry_method,
                    net_return_1d, net_return_3d, net_return_5d,
                    benchmark_return_3d, excess_return_3d, max_return_3d,
                    max_drawdown_3d, stop_hit_at, first_barrier, success_t3,
                    matured_horizon, outcome_status, evaluated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    entry_at=excluded.entry_at, entry_price=excluded.entry_price,
                    entry_method=excluded.entry_method,
                    net_return_1d=excluded.net_return_1d,
                    net_return_3d=excluded.net_return_3d,
                    net_return_5d=excluded.net_return_5d,
                    benchmark_return_3d=excluded.benchmark_return_3d,
                    excess_return_3d=excluded.excess_return_3d,
                    max_return_3d=excluded.max_return_3d,
                    max_drawdown_3d=excluded.max_drawdown_3d,
                    stop_hit_at=excluded.stop_hit_at,
                    first_barrier=excluded.first_barrier,
                    success_t3=excluded.success_t3,
                    matured_horizon=excluded.matured_horizon,
                    outcome_status=excluded.outcome_status,
                    evaluated_at=excluded.evaluated_at,
                    updated_at=excluded.updated_at
                """,
                (
                    int(row["prediction_id"]),
                    row.get("entry_date"),
                    row.get("entry_price"),
                    row.get("entry_method"),
                    row.get("net_return_1d"),
                    row.get("net_return_3d"),
                    row.get("net_return_5d"),
                    row.get("benchmark_return_3d"),
                    row.get("excess_return_3d"),
                    row.get("max_return_3d"),
                    row.get("max_drawdown_3d"),
                    row.get("stop_loss_date"),
                    "stop" if row.get("stop_loss_hit") else None,
                    row.get("success_t3"),
                    int(row.get("matured_horizon") or 0),
                    row.get("outcome_status") or "pending",
                    row.get("tested_at"),
                    now,
                ),
            )
        conn.commit()
    return len(rows)


def collect_news_research(run_id, db_path=DB_PATH, limit=MAX_NEWS_CANDIDATES):
    with get_connection(db_path) as conn:
        init_db(conn)
        candidates = conn.execute(
            """
            SELECT ce.code, ce.name, ce.strategies_json, ce.snapshot_json
            FROM candidate_events ce
            WHERE ce.run_id=? AND ce.is_selected=1
            ORDER BY ce.selection_rank, ce.id
            LIMIT ?
            """,
            (int(run_id), int(limit)),
        ).fetchall()

    results = []
    now = get_taipei_now().isoformat(timespec="seconds")
    for candidate in candidates:
        candidate = dict(candidate)
        snapshot = _decode_json(candidate.get("snapshot_json"), {})
        evidence = fetch_google_news_evidence(
            f"{candidate['code']} {candidate['name']}", limit=5
        )
        try:
            analysis = analyze_news_evidence_claude(
                candidate.get("name") or "",
                candidate["code"],
                _decode_json(candidate.get("strategies_json"), []),
                snapshot.get("條件", ""),
                evidence,
            )
        except Exception as exc:
            analysis = {
                "status": "failed",
                "sentiment": None,
                "confidence": None,
                "news_score": None,
                "catalyst_score": None,
                "risk_score": None,
                "summary": f"LLM 新聞判讀失敗：{exc.__class__.__name__}",
                "model": os.getenv("ANTHROPIC_MODEL", ""),
            }

        with get_connection(db_path) as conn:
            init_db(conn)
            for item in evidence:
                digest = hashlib.sha256(
                    f"{item.get('url')}|{item.get('title')}".encode("utf-8")
                ).hexdigest()
                conn.execute(
                    """
                    INSERT INTO news_evidence (
                        run_id, code, title, source_name, url, published_at,
                        known_at, evidence_type, sentiment, confidence,
                        content_hash, extracted_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'news', ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, code, url, published_at) DO UPDATE SET
                        title=excluded.title, source_name=excluded.source_name,
                        known_at=excluded.known_at, sentiment=excluded.sentiment,
                        confidence=excluded.confidence,
                        content_hash=excluded.content_hash,
                        extracted_json=excluded.extracted_json
                    """,
                    (
                        int(run_id),
                        candidate["code"],
                        item.get("title") or "",
                        item.get("source_name") or "",
                        item.get("url") or "",
                        item.get("published_at") or "",
                        now,
                        analysis.get("sentiment"),
                        analysis.get("confidence"),
                        digest,
                        json.dumps(analysis, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE feature_snapshots
                SET news_score=?, catalyst_score=?, risk_score=?
                WHERE run_id=? AND code=? AND feature_version=?
                """,
                (
                    analysis.get("news_score"),
                    analysis.get("catalyst_score"),
                    analysis.get("risk_score"),
                    int(run_id),
                    candidate["code"],
                    FEATURE_VERSION,
                ),
            )
            conn.commit()
        results.append(
            {
                "code": candidate["code"],
                "name": candidate.get("name") or "",
                "evidence_count": len(evidence),
                **analysis,
            }
        )
    return results


def format_ai_report(training, predictions, news):
    lines = ["", "AI 影子研究（目前不改變正式名單）："]
    if training.get("status") != "trained":
        lines.append(
            f"- 量化模型尚未產生預測：{training.get('message') or training.get('status')}"
        )
    else:
        metrics = training.get("metrics", {})
        auc = metrics.get("validation_auc")
        auc_text = "NA" if auc is None else f"{auc:.3f}"
        lines.append(
            f"- 模型 {training['version']}｜樣本{metrics.get('samples', 0)}｜"
            f"時序驗證 AUC {auc_text}；僅供影子比較。"
        )
        selected = [row for row in predictions if row.get("is_selected")]
        if selected:
            for row in selected:
                label = "AI影子" if row.get("is_prospective") else "AI歷史試跑"
                lines.append(
                    f"- {label} {row['code']} {row['name']}｜"
                    f"T+3成功機率{row['probability_t3'] * 100:.1f}%｜"
                    f"預期超額{row['expected_excess_return_3d']:.2f}%｜"
                    f"預期回撤{row['expected_max_drawdown_3d']:.2f}%"
                )
        else:
            lines.append("- 本批次沒有符合既有風控與每日首次合格條件的 AI 影子入選。")
    for item in news:
        lines.append(
            f"- 新聞AI {item['code']} {item['name']}｜證據{item['evidence_count']}則｜"
            f"{item.get('sentiment') or '未判讀'}｜{item.get('summary') or '無摘要'}"
        )
    return "\n".join(lines)


def run_ai_pipeline(
    run_id=None,
    db_path=DB_PATH,
    collect_news=True,
    backfill_features=True,
    train=True,
    predict=True,
):
    run_id = int(run_id) if run_id is not None else latest_candidate_run_id(db_path)
    if run_id is None:
        return {"status": "skipped", "reason": "no_candidate_run", "report_text": ""}

    feature_count = build_feature_snapshots(
        db_path=db_path,
        run_id=None if backfill_features else run_id,
        missing_only=True,
    )
    build_feature_snapshots(db_path=db_path, run_id=run_id, missing_only=True)
    training = train_shadow_model(db_path=db_path) if train else {"status": "disabled", "model": None}
    predictions = (
        save_shadow_predictions(run_id, training, db_path=db_path)
        if predict and training.get("model")
        else []
    )
    outcomes_updated = update_prediction_outcomes(db_path=db_path)
    news = collect_news_research(run_id, db_path=db_path) if collect_news else []
    return {
        "status": "completed",
        "run_id": run_id,
        "features_saved": feature_count,
        "outcomes_updated": outcomes_updated,
        "training": {key: value for key, value in training.items() if key != "model"},
        "predictions": predictions,
        "news": news,
        "report_text": format_ai_report(training, predictions, news),
    }


def main():
    parser = argparse.ArgumentParser(description="Run the auditable AI shadow pipeline.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--no-news", action="store_true")
    parser.add_argument("--no-backfill-features", action="store_true")
    parser.add_argument("--no-train", action="store_true")
    parser.add_argument("--no-predict", action="store_true")
    args = parser.parse_args()
    result = run_ai_pipeline(
        run_id=args.run_id,
        db_path=args.db,
        collect_news=not args.no_news,
        backfill_features=not args.no_backfill_features,
        train=not args.no_train,
        predict=not args.no_predict,
    )
    printable = dict(result)
    printable["predictions"] = len(result.get("predictions", []))
    printable["news"] = len(result.get("news", []))
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
