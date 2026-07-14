import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkflowPersistenceTests(unittest.TestCase):
    def test_workflows_use_conflict_safe_persistence_script(self):
        for workflow_name in ("intraday_scan.yml", "daily_scan.yml", "global_market.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn("git pull --ff-only origin main", workflow)
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

    def test_vercel_owns_all_automation_schedules(self):
        workflow = (ROOT / ".github" / "workflows" / "intraday_scan.yml").read_text()
        daily_workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        market_workflow = (ROOT / ".github" / "workflows" / "global_market.yml").read_text()
        vercel = (ROOT / "web" / "vercel.json").read_text()

        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("schedule:", daily_workflow)
        self.assertNotIn("schedule:", market_workflow)
        self.assertIn('"schedule": "0,30 1-5 * * 1-5"', vercel)
        self.assertIn('"schedule": "17 6 * * 1-5"', vercel)
        self.assertIn('"schedule": "17 * * * 1-5"', vercel)
        self.assertIn('SCHEDULED_CRON: ${{ inputs.scheduled_cron }}', workflow)

    def test_vercel_cron_routes_fail_closed_with_cron_secret(self):
        for route_name in ("intraday", "daily", "market"):
            route = (
                ROOT / "web" / "src" / "app" / "api" / "cron" / route_name / "route.ts"
            ).read_text()
            self.assertIn("process.env.CRON_SECRET", route)
            self.assertIn("cronAuthorized", route)
            self.assertIn("process.env.GITHUB_ACTIONS_TOKEN", route)

    def test_daily_workflow_backtests_all_candidates_before_training(self):
        workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        candidate_index = workflow.index("python candidate_backtest.py --limit 400")
        training_index = workflow.index("python ai_pipeline.py --no-news --no-predict")
        paper_index = workflow.index("python paper_trading.py")
        self.assertLess(candidate_index, training_index)
        self.assertLess(training_index, paper_index)

    def test_automated_data_commits_use_conventional_english_messages(self):
        for workflow_name in ("intraday_scan.yml", "daily_scan.yml", "global_market.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertRegex(
                workflow,
                r'bash persist_scanner_data\.sh "chore\(data\): (record|refresh)',
            )


if __name__ == "__main__":
    unittest.main()
