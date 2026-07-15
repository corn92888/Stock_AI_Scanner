import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from ai_pipeline import (
    FEATURE_VERSION,
    MODEL_FEATURES,
    _load_historical_replay_training_frame,
)
from database import get_connection, init_db


DATASET_VERSION = "point_in_time_replay_training_v2"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_execution_labels(frame, execution_labels_path):
    if not execution_labels_path:
        return frame, None
    execution_labels_path = Path(execution_labels_path)
    if not execution_labels_path.exists():
        raise FileNotFoundError(
            f"Replay execution label dataset not found: {execution_labels_path}"
        )
    labels = pd.read_csv(
        execution_labels_path, dtype={"code": str, "trade_date": str}
    )
    required = {"trade_date", "code", "scenario_version"}
    missing = sorted(required - set(labels.columns))
    if missing:
        raise ValueError(
            "Replay execution label dataset missing columns: " + ", ".join(missing)
        )
    if labels.duplicated(["trade_date", "code"]).any():
        raise ValueError("Replay execution label dataset contains duplicate event keys.")
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["code"] = result["code"].astype(str)
    labels["trade_date"] = labels["trade_date"].astype(str)
    labels["code"] = labels["code"].astype(str)
    merged = result.merge(
        labels.drop(columns=["source_event_id"], errors="ignore"),
        on=["trade_date", "code"],
        how="left",
        validate="one_to_one",
    )
    coverage = float(merged["scenario_version"].notna().mean()) if len(merged) else 0.0
    return merged, {
        "path": execution_labels_path.name,
        "sha256": _sha256(execution_labels_path),
        "coverage_pct": round(coverage * 100, 4),
        "rows": int(len(labels)),
    }


def export_replay_training_dataset(
    database_path, output_path, execution_labels_path=None
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as conn:
        init_db(conn)
        frame = _load_historical_replay_training_frame(conn)
    if frame.empty:
        raise ValueError("No mature point-in-time replay samples are available.")
    frame, execution_labels = _merge_execution_labels(
        frame, execution_labels_path
    )
    frame = frame.sort_values(["trade_date", "code", "feature_id"]).reset_index(drop=True)
    frame.to_csv(
        output_path,
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    metadata = {
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "model_features": MODEL_FEATURES,
        "samples": int(len(frame)),
        "positive_samples": int(frame["success_t3"].sum()),
        "trade_dates": int(frame["trade_date"].nunique()),
        "start_date": str(frame["trade_date"].min()),
        "end_date": str(frame["trade_date"].max()),
        "symbols": int(frame["code"].nunique()),
        "source": "official_point_in_time_replay",
        "execution_labels": execution_labels,
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
        description="Export compact point-in-time samples for governed AI training."
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execution-labels")
    args = parser.parse_args()
    result = export_replay_training_dataset(
        args.database, args.output, execution_labels_path=args.execution_labels
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
