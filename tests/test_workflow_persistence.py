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

    def test_intraday_workflow_installs_only_runtime_dependencies(self):
        workflow = (ROOT / ".github" / "workflows" / "intraday_scan.yml").read_text()
        self.assertIn("pip install --prefer-binary -r requirements-intraday.txt", workflow)
        self.assertNotIn("pip install -r requirements.txt", workflow)

    def test_intraday_workflow_has_all_half_hour_market_slots(self):
        workflow = (ROOT / ".github" / "workflows" / "intraday_scan.yml").read_text()
        expected_crons = (
            "0 1 * * 1-5",
            "30 1 * * 1-5",
            "0 2 * * 1-5",
            "30 2 * * 1-5",
            "0 3 * * 1-5",
            "30 3 * * 1-5",
            "0 4 * * 1-5",
            "30 4 * * 1-5",
            "0 5 * * 1-5",
            "30 5 * * 1-5",
        )

        for cron in expected_crons:
            self.assertEqual(workflow.count(f"cron: '{cron}'"), 1)
        self.assertIn('EVENT_SCHEDULE: ${{ github.event.schedule }}', workflow)

    def test_daily_workflow_backtests_all_candidates_before_training(self):
        workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        candidate_index = workflow.index("python candidate_backtest.py --limit 400")
        training_index = workflow.index("python ai_pipeline.py --no-news --no-predict")
        self.assertLess(candidate_index, training_index)

    def test_automated_data_commits_use_conventional_english_messages(self):
        for workflow_name in ("intraday_scan.yml", "daily_scan.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn('bash persist_scanner_data.sh "chore(data): record', workflow)


if __name__ == "__main__":
    unittest.main()
