import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkflowPersistenceTests(unittest.TestCase):
    def test_workflows_use_conflict_safe_persistence_script(self):
        for workflow_name in ("intraday_scan.yml", "daily_scan.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn("bash persist_scanner_data.sh", workflow)
            self.assertNotIn("git pull --rebase origin main\n            git push", workflow)

    def test_snapshot_is_exported_after_main_is_synchronized(self):
        script = (ROOT / "persist_scanner_data.sh").read_text()
        pull_index = script.index("git pull --rebase origin main")
        export_index = script.index("python export_dashboard_snapshot.py")
        commit_index = script.index('git commit -m "$commit_message"')

        self.assertLess(pull_index, export_index)
        self.assertLess(export_index, commit_index)


if __name__ == "__main__":
    unittest.main()
