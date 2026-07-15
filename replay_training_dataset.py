import argparse
import hashlib
import json
from pathlib import Path

from ai_pipeline import (
    FEATURE_VERSION,
    MODEL_FEATURES,
    _load_historical_replay_training_frame,
)
from database import get_connection, init_db


DATASET_VERSION = "point_in_time_replay_training_v1"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_replay_training_dataset(database_path, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as conn:
        init_db(conn)
        frame = _load_historical_replay_training_frame(conn)
    if frame.empty:
        raise ValueError("No mature point-in-time replay samples are available.")
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
    args = parser.parse_args()
    result = export_replay_training_dataset(args.database, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
