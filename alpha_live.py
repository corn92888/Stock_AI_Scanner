import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
import yfinance as yf

from alpha_strategy_v2 import (
    ALPHA_MODEL_ARTIFACT_VERSION,
    ALPHA_MODEL_VERSION,
    DEFAULT_MODEL_OUTPUT,
    AlphaSpec,
    _anti_chase_pool,
    _diversified_top,
    _feature_frame,
)
from alpha_universe_dataset import (
    ALPHA_DATASET_VERSION,
    ALPHA_EXECUTION_VERSION,
    AlphaUniverseConfig,
    build_stock_feature_frame,
    finalize_alpha_inference_panel,
)
from database import DB_PATH, get_connection, get_taipei_now, init_db


ALPHA_LIVE_VERSION = "alpha_full_universe_eod_v1"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_artifact(path=DEFAULT_MODEL_OUTPUT):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Alpha v2 model artifact not found: {path}")
    artifact = joblib.load(path)
    required = {
        "artifact_version",
        "model_version",
        "dataset_version",
        "execution_version",
        "dataset_fingerprint",
        "spec",
        "confidence_threshold",
        "dependency_versions",
        "model",
    }
    missing = sorted(required - set(artifact))
    if missing:
        raise ValueError("Alpha model artifact missing fields: " + ", ".join(missing))
    if artifact["artifact_version"] != ALPHA_MODEL_ARTIFACT_VERSION:
        raise ValueError("Unsupported Alpha model artifact version")
    if artifact["model_version"] != ALPHA_MODEL_VERSION:
        raise ValueError("Unsupported Alpha model version")
    if artifact["dataset_version"] != ALPHA_DATASET_VERSION:
        raise ValueError("Unsupported Alpha dataset version")
    if artifact["execution_version"] != ALPHA_EXECUTION_VERSION:
        raise ValueError("Unsupported Alpha execution version")
    runtime_versions = {
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
    }
    if artifact["dependency_versions"] != runtime_versions:
        raise ValueError(
            "Alpha model runtime mismatch: "
            f"artifact={artifact['dependency_versions']} runtime={runtime_versions}"
        )
    return artifact


def _metadata_for_code(codes, code, ticker):
    info = codes.get(code) if hasattr(codes, "get") else None
    return {
        "name": getattr(info, "name", "") or "",
        "industry": getattr(info, "group", "") or "其他",
        "market": getattr(info, "market", "")
        or ("上櫃" if str(ticker).endswith(".TWO") else "上市"),
    }


def build_live_panel(histories, yf_to_code, codes, benchmark, trade_date=None):
    if benchmark is None or benchmark.empty:
        raise ValueError("Alpha live scoring requires the ^TWII benchmark history")
    inferred_date = str(
        trade_date
        or max(
            pd.Timestamp(frame.index.max()).date()
            for frame in histories.values()
            if frame is not None and not frame.empty
        )
    )
    config = AlphaUniverseConfig(start_date=inferred_date, end_date=inferred_date)

    def build_one(item):
        ticker, raw = item
        code = str(yf_to_code.get(ticker) or "")
        if not code or raw is None or raw.empty:
            return pd.DataFrame()
        history = raw.copy()
        for column, value in _metadata_for_code(codes, code, ticker).items():
            history[column] = value
        frame = build_stock_feature_frame(code, history, benchmark, config)
        if frame.empty:
            return frame
        return frame[frame["trade_date"] == inferred_date].copy()

    frames = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(build_one, item) for item in histories.items()]
        for future in as_completed(futures):
            frame = future.result()
            if not frame.empty:
                frames.append(frame)
    panel = finalize_alpha_inference_panel(
        frames,
        config,
        trade_date=inferred_date,
    )
    return panel, {
        "signal_date": inferred_date,
        "history_symbols": int(len(histories)),
        "feature_symbols": int(sum(len(frame) for frame in frames)),
        "eligible_symbols": int(len(panel)),
    }


def score_alpha_panel(panel, artifact):
    if panel.empty:
        return pd.DataFrame(), {
            "status": "blocked",
            "reason": "empty_feature_panel",
            "confidence": None,
        }
    spec = AlphaSpec(**artifact["spec"])
    pool = _anti_chase_pool(panel)
    if pool.empty:
        return pool, {
            "status": "abstained",
            "reason": "anti_chase_pool_empty",
            "confidence": None,
            "eligible_after_risk": 0,
        }
    pool = pool.copy()
    features = _feature_frame(pool)
    expected_features = artifact.get("feature_columns")
    if expected_features and list(features.columns) != list(expected_features):
        raise ValueError("Alpha live feature schema does not match the model artifact")
    pool["predicted_alpha"] = artifact["model"].predict(features)
    selected = _diversified_top(pool, "predicted_alpha", spec.top_k)
    confidence = (
        float(selected["predicted_alpha"].mean()) if not selected.empty else None
    )
    threshold = float(artifact["confidence_threshold"])
    active = confidence is not None and confidence > threshold
    if not active:
        selected = selected.iloc[0:0].copy()
    else:
        selected = selected.sort_values(
            ["predicted_alpha", "turnover_20d_billion", "code"],
            ascending=[False, False, True],
            kind="stable",
        ).copy()
        selected["rank_order"] = range(1, len(selected) + 1)
        selected["allocation_weight"] = 1.0 / max(spec.top_k, 1)
        selected["holding_horizon"] = spec.horizon
    return selected, {
        "status": "active" if active else "abstained",
        "reason": "" if active else "confidence_below_threshold",
        "confidence": confidence,
        "confidence_threshold": threshold,
        "eligible_after_risk": int(len(pool)),
    }


def save_alpha_live_run(
    selected,
    diagnostics,
    artifact,
    artifact_fingerprint,
    db_path=DB_PATH,
):
    now = get_taipei_now().isoformat(timespec="seconds")
    signal_date = str(diagnostics["signal_date"])
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO alpha_live_runs (
                signal_date, generated_at, model_version, artifact_fingerprint,
                dataset_fingerprint, status, confidence, confidence_threshold,
                universe_count, eligible_count, selected_count, diagnostics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_date, model_version, artifact_fingerprint) DO UPDATE SET
                generated_at=excluded.generated_at,
                dataset_fingerprint=excluded.dataset_fingerprint,
                status=excluded.status,
                confidence=excluded.confidence,
                confidence_threshold=excluded.confidence_threshold,
                universe_count=excluded.universe_count,
                eligible_count=excluded.eligible_count,
                selected_count=excluded.selected_count,
                diagnostics_json=excluded.diagnostics_json
            """,
            (
                signal_date,
                now,
                artifact["model_version"],
                artifact_fingerprint,
                artifact["dataset_fingerprint"],
                diagnostics["status"],
                diagnostics.get("confidence"),
                float(artifact["confidence_threshold"]),
                int(diagnostics.get("history_symbols", 0)),
                int(diagnostics.get("eligible_symbols", 0)),
                int(len(selected)),
                json.dumps(diagnostics, ensure_ascii=True, sort_keys=True),
            ),
        )
        run_id = conn.execute(
            """
            SELECT id FROM alpha_live_runs
            WHERE signal_date=? AND model_version=? AND artifact_fingerprint=?
            """,
            (signal_date, artifact["model_version"], artifact_fingerprint),
        ).fetchone()["id"]
        conn.execute("DELETE FROM alpha_live_signals WHERE run_id=?", (run_id,))
        for row in selected.to_dict("records"):
            conn.execute(
                """
                INSERT INTO alpha_live_signals (
                    run_id, code, name, industry, rank_order, signal_price,
                    predicted_alpha, allocation_weight, holding_horizon, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(row["code"]),
                    row.get("name") or "",
                    row.get("industry") or "其他",
                    int(row["rank_order"]),
                    float(row["signal_price"]),
                    float(row["predicted_alpha"]),
                    float(row["allocation_weight"]),
                    int(row["holding_horizon"]),
                    now,
                ),
            )
    return int(run_id)


def run_alpha_live_scoring(
    histories,
    yf_to_code,
    codes,
    benchmark,
    model_path=DEFAULT_MODEL_OUTPUT,
    db_path=DB_PATH,
    trade_date=None,
):
    artifact = load_model_artifact(model_path)
    panel, panel_diagnostics = build_live_panel(
        histories,
        yf_to_code,
        codes,
        benchmark,
        trade_date=trade_date,
    )
    selected, score_diagnostics = score_alpha_panel(panel, artifact)
    diagnostics = {
        "version": ALPHA_LIVE_VERSION,
        **panel_diagnostics,
        **score_diagnostics,
    }
    artifact_fingerprint = _sha256(model_path)
    run_id = save_alpha_live_run(
        selected,
        diagnostics,
        artifact,
        artifact_fingerprint,
        db_path=db_path,
    )
    return {
        "run_id": run_id,
        "model_version": artifact["model_version"],
        "artifact_fingerprint": artifact_fingerprint,
        "selected": selected.to_dict("records"),
        **diagnostics,
    }


def _download_live_histories(period="2y"):
    import twstock

    codes = twstock.codes
    tickers = [code for code in codes if codes[code].type == "股票"]
    yf_to_code = {
        f"{code}.{'TWO' if codes[code].market == '上櫃' else 'TW'}": code
        for code in tickers
    }
    from scanner import batch_download

    histories = batch_download(list(yf_to_code), period=period, chunk_size=200)
    benchmark = yf.download(
        "^TWII", period=period, progress=False, auto_adjust=False
    )
    return histories, yf_to_code, codes, benchmark


def main():
    parser = argparse.ArgumentParser(
        description="Generate governed Alpha v2 EOD paper signals from the liquid universe."
    )
    parser.add_argument("--model", default=str(DEFAULT_MODEL_OUTPUT))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--trade-date")
    args = parser.parse_args()
    histories, yf_to_code, codes, benchmark = _download_live_histories()
    result = run_alpha_live_scoring(
        histories,
        yf_to_code,
        codes,
        benchmark,
        model_path=args.model,
        db_path=args.db,
        trade_date=args.trade_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
