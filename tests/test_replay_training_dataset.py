import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
