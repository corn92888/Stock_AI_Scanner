import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkflowPersistenceTests(unittest.TestCase):
    def test_workflows_use_conflict_safe_persistence_script(self):
        for workflow_name in (
            "intraday_scan.yml",
            "daily_scan.yml",
            "global_market.yml",
            "historical_replay.yml",
            "institutional_flow.yml",
            "institutional_research.yml",
        ):
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
        institutional_workflow = (
            ROOT / ".github" / "workflows" / "institutional_flow.yml"
        ).read_text()
        vercel = (ROOT / "web" / "vercel.json").read_text()

        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("schedule:", daily_workflow)
        self.assertNotIn("schedule:", market_workflow)
        self.assertNotIn("schedule:", institutional_workflow)
        self.assertIn('"schedule": "0,30 1-5 * * 1-5"', vercel)
        self.assertIn('"schedule": "17 6 * * 1-5"', vercel)
        self.assertIn('"schedule": "17 * * * 1-5"', vercel)
        self.assertIn('"schedule": "20 11 * * 1-5"', vercel)
        self.assertIn('SCHEDULED_CRON: ${{ inputs.scheduled_cron }}', workflow)

    def test_vercel_cron_routes_fail_closed_with_cron_secret(self):
        for route_name in ("intraday", "daily", "market", "institutional"):
            route = (
                ROOT / "web" / "src" / "app" / "api" / "cron" / route_name / "route.ts"
            ).read_text()
            self.assertIn("process.env.CRON_SECRET", route)
            self.assertIn("cronAuthorized", route)
            self.assertIn("process.env.GITHUB_ACTIONS_TOKEN", route)

    def test_daily_workflow_backtests_all_candidates_before_training(self):
        workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        candidate_index = workflow.index("python candidate_backtest.py --limit 400")
        scenario_index = workflow.index(
            "python candidate_execution_research.py --limit 1000"
        )
        training_index = workflow.index("python ai_pipeline.py --no-news")
        evaluation_index = workflow.index("python research_evaluation.py")
        paper_index = workflow.index("python paper_trading.py")
        monitor_index = workflow.index("python research_monitor.py")
        export_index = workflow.index("python export_dashboard_snapshot.py")
        self.assertLess(candidate_index, training_index)
        self.assertLess(candidate_index, scenario_index)
        self.assertLess(scenario_index, training_index)
        self.assertLess(training_index, evaluation_index)
        self.assertLess(evaluation_index, paper_index)
        self.assertLess(training_index, paper_index)
        self.assertLess(paper_index, monitor_index)
        self.assertLess(monitor_index, export_index)

    def test_historical_replay_resumes_and_generates_attribution(self):
        workflow = (ROOT / ".github" / "workflows" / "historical_replay.yml").read_text()
        universe_index = workflow.index("python historical_universe.py")
        replay_index = workflow.index(
            'args=(--start "$START_DATE" --end "$END_DATE" --db-path "$REPLAY_DB")'
        )
        attribution_index = workflow.index("python replay_attribution.py")
        execution_index = workflow.index("python execution_research.py")
        dataset_index = workflow.index("python replay_training_dataset.py")
        model_index = workflow.index("python ai_pipeline.py")
        merge_index = workflow.index("python merge_historical_replay.py")
        evaluation_index = workflow.index("python research_evaluation.py")
        archive_index = workflow.index("python archive_historical_replay.py")
        release_index = workflow.index("gh release upload research-replay-data-v1")
        persistence_index = workflow.index("bash persist_scanner_data.sh")

        self.assertIn("actions/cache@v4", workflow)
        self.assertIn("args+=(--resume)", workflow)
        self.assertIn("--universe-file data/universe_history.csv", workflow)
        self.assertIn("group: stock-scanner-historical-replay", workflow)
        self.assertIn("group: stock-scanner-automation", workflow)
        self.assertIn("$RUNNER_TEMP/historical_replay.db", workflow)
        self.assertIn("gh release download research-replay-data-v1", workflow)
        self.assertIn("Restore durable replay state", workflow)
        self.assertIn('cp "$restore_dir"/universe_history* data/', workflow)
        self.assertIn(
            '[ -z "$UNIVERSE_FILE" ] && [ ! -f data/universe_history.csv ]',
            workflow,
        )
        self.assertIn(
            'compgen -G "$RUNNER_TEMP/replay_training_samples.csv.gz*"', workflow
        )
        self.assertIn("replay_execution_labels.csv.gz", workflow)
        self.assertIn('--execution-labels "$REPLAY_EXECUTION_DATASET"', workflow)
        self.assertIn("if: ${{ needs.replay.result == 'success' }}", workflow)
        self.assertNotIn(
            "always() && needs.replay.result != 'cancelled'", workflow
        )
        self.assertIn("if: always()", workflow)
        self.assertIn("retention-days: 30", workflow)
        self.assertLess(universe_index, replay_index)
        self.assertLess(replay_index, attribution_index)
        self.assertLess(attribution_index, execution_index)
        self.assertLess(execution_index, dataset_index)
        self.assertLess(attribution_index, dataset_index)
        self.assertLess(dataset_index, model_index)
        self.assertLess(model_index, archive_index)
        self.assertLess(attribution_index, archive_index)
        self.assertLess(archive_index, release_index)
        self.assertLess(release_index, merge_index)
        self.assertLess(attribution_index, merge_index)
        self.assertLess(merge_index, evaluation_index)
        self.assertLess(evaluation_index, persistence_index)
        self.assertLess(merge_index, persistence_index)

    def test_institutional_research_waits_for_complete_warmup_shards(self):
        workflow = (
            ROOT / ".github" / "workflows" / "institutional_research.yml"
        ).read_text()
        dataset_index = workflow.index("python institutional_replay_dataset.py")
        evaluation_index = workflow.index("python institutional_research.py")
        attribution_index = workflow.index("python institutional_attribution.py")
        interaction_index = workflow.index(
            "python institutional_conditional_research.py"
        )
        release_index = workflow.index("gh release upload research-institutional-data-v1")
        persistence_index = workflow.index("bash persist_scanner_data.sh")

        self.assertIn("workflow_run:", workflow)
        self.assertIn("for year in $(seq 2020 2025)", workflow)
        self.assertIn("ready=false", workflow)
        self.assertIn("steps.shards.outputs.ready == 'true'", workflow)
        self.assertIn(
            '--attribution "$RUNNER_TEMP/institutional_attribution.json"', workflow
        )
        self.assertNotIn("--allow-partial-shards", workflow)
        self.assertLess(dataset_index, evaluation_index)
        self.assertLess(evaluation_index, attribution_index)
        self.assertLess(attribution_index, interaction_index)
        self.assertLess(interaction_index, release_index)
        self.assertLess(release_index, persistence_index)

    def test_automated_data_commits_use_conventional_english_messages(self):
        for workflow_name in (
            "intraday_scan.yml",
            "daily_scan.yml",
            "global_market.yml",
            "historical_replay.yml",
            "institutional_flow.yml",
            "institutional_research.yml",
            "learnability_audit.yml",
        ):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertRegex(
                workflow,
                r'bash persist_scanner_data\.sh "chore\(data\): (record|refresh)',
            )

    def test_learnability_audit_runs_after_replay_and_preserves_holdout(self):
        workflow = (
            ROOT / ".github" / "workflows" / "learnability_audit.yml"
        ).read_text()
        audit_index = workflow.index("python candidate_learnability_audit.py")
        artifact_index = workflow.index("actions/upload-artifact@v4")
        persistence_index = workflow.index("bash persist_scanner_data.sh")

        self.assertIn("workflow_run:", workflow)
        self.assertIn("Historical Point-in-Time Replay", workflow)
        self.assertIn("data/replay_training_samples.csv.gz", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertLess(audit_index, artifact_index)
        self.assertLess(artifact_index, persistence_index)


if __name__ == "__main__":
    unittest.main()
