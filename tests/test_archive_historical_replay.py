import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from archive_historical_replay import build_replay_archive
from tests.test_merge_historical_replay import _seed_replay


class HistoricalReplayArchiveTests(unittest.TestCase):
    def test_archive_contains_database_universe_and_verified_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "source.db"
            universe = root / "universe_history.csv"
            output = root / "replay.tar.gz"
            dataset = root / "replay_training_samples.csv.gz"
            _seed_replay(database)
            universe.write_text("code,listed_on\n2330,1962-02-09\n", encoding="utf-8")
            dataset.write_bytes(b"training-data")

            result = build_replay_archive(
                database,
                output,
                start_date="2025-01-01",
                end_date="2025-12-31",
                universe_files=[universe],
                extra_files=[dataset],
            )

            self.assertEqual(result["replay"]["events"], 1)
            self.assertEqual(len(result["archive_sha256"]), 64)
            with tarfile.open(output, "r:gz") as archive:
                self.assertEqual(
                    set(archive.getnames()),
                    {
                        "historical_replay.db",
                        "manifest.json",
                        "universe_history.csv",
                        "replay_training_samples.csv.gz",
                    },
                )
                manifest = json.load(archive.extractfile("manifest.json"))
            self.assertEqual(manifest["schema_version"], "historical_replay_archive_v1")
            self.assertEqual(manifest["replay"]["replay_key"], "replay-key")
            self.assertEqual(manifest["universe_files"], ["universe_history.csv"])
            self.assertEqual(
                manifest["included_files"], ["replay_training_samples.csv.gz"]
            )


if __name__ == "__main__":
    unittest.main()
