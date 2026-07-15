import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_pipeline import _load_replay_training_dataset
from replay_training_dataset import export_replay_training_dataset
from tests.test_merge_historical_replay import _seed_replay


class ReplayTrainingDatasetTests(unittest.TestCase):
    def test_exports_deterministic_point_in_time_training_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "replay.db"
            output = root / "training.csv.gz"
            _seed_replay(database)

            result = export_replay_training_dataset(database, output)
            frame = _load_replay_training_dataset(output)
            metadata = json.loads(
                Path(result["metadata"]).read_text(encoding="utf-8")
            )

            self.assertEqual(result["samples"], 1)
            self.assertEqual(result["positive_samples"], 1)
            self.assertEqual(len(result["sha256"]), 64)
            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.iloc[0]["training_source"], "point_in_time_replay")
            self.assertEqual(metadata["source"], "official_point_in_time_replay")

    def test_merges_versioned_execution_labels_by_event_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "replay.db"
            output = root / "training.csv.gz"
            labels = root / "execution.csv.gz"
            _seed_replay(database)
            pd.DataFrame(
                [
                    {
                        "source_event_id": 1,
                        "trade_date": "2025-01-02",
                        "code": "2330",
                        "scenario_version": "scenario_test_v1",
                        "next_open_net_return_5d": 3.25,
                    }
                ]
            ).to_csv(labels, index=False, compression="gzip")

            result = export_replay_training_dataset(
                database, output, execution_labels_path=labels
            )
            frame = pd.read_csv(output, dtype={"code": str})

            self.assertEqual(frame.iloc[0]["scenario_version"], "scenario_test_v1")
            self.assertEqual(frame.iloc[0]["next_open_net_return_5d"], 3.25)
            self.assertEqual(result["execution_labels"]["coverage_pct"], 100.0)
            self.assertEqual(len(result["execution_labels"]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
