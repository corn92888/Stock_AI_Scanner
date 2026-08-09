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
            "learnability_audit.yml",
            "cloud_evidence_audit.yml",
        ):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn("git pull --ff-only origin main", workflow)
            self.assertIn("bash restore_scanner_data.sh", workflow)
            self.assertIn("bash persist_scanner_data.sh", workflow)
            self.assertIn("SUPABASE_SERVICE_ROLE_KEY", workflow)
            self.assertIn("CLOUD_EVIDENCE_MODE", workflow)
            self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
            self.assertNotIn("git pull --rebase origin main\n            git push", workflow)

    def test_snapshot_is_exported_after_main_is_synchronized(self):
        script = (ROOT / "persist_scanner_data.sh").read_text()
        pull_index = script.index("git pull --rebase origin main")
        cloud_index = script.index(
            '"$python_bin" cloud_evidence.py "${cloud_args[@]}"'
        )
        export_index = script.index('"$python_bin" export_dashboard_snapshot.py')
        release_index = script.index('gh_release upload "$release_tag"')
        commit_index = script.index('git commit -m "$commit_message"')

        self.assertLess(pull_index, export_index)
        self.assertLess(pull_index, cloud_index)
        self.assertLess(cloud_index, export_index)
        self.assertLess(export_index, release_index)
        self.assertLess(release_index, commit_index)
        self.assertLess(export_index, commit_index)

    def test_daily_workflow_creates_one_verified_archive_per_trade_date(self):
        workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        script = (ROOT / "persist_scanner_data.sh").read_text()
        self.assertIn('CLOUD_EVIDENCE_ARCHIVE: "true"', workflow)
        self.assertIn("cloud_args+=(--archive-daily)", script)
        self.assertIn("cloud_args+=(--required)", script)
        self.assertIn('"$python_bin" cloud_evidence.py prune', script)
        self.assertIn("CLOUD_EVIDENCE_RETENTION_DAYS", script)

    def test_dual_write_uses_a_release_asset_instead_of_a_git_sqlite_blob(self):
        restore_script = (ROOT / "restore_scanner_data.sh").read_text()
        persist_script = (ROOT / "persist_scanner_data.sh").read_text()
        gitignore = (ROOT / ".gitignore").read_text()

        self.assertIn("cloud_primary)", restore_script)
        self.assertIn('args=(restore --database "$database_path" --required)', restore_script)
        self.assertIn("scanner-live-data-v1", restore_script)
        self.assertIn('gh_release download "$release_tag"', restore_script)
        self.assertIn('if [ "$migration_mode" = "dual_write" ]; then', persist_script)
        self.assertIn('gh_release upload "$release_tag"', persist_script)
        self.assertNotIn("repository_args", restore_script)
        self.assertNotIn("repository_args", persist_script)
        self.assertNotIn("git add data/stock_scanner.db", persist_script)
        self.assertIn("data/stock_scanner.db", gitignore)

    def test_manual_cloud_audit_publishes_a_machine_readable_gate(self):
        workflow = (
            ROOT / ".github" / "workflows" / "cloud_evidence_audit.yml"
        ).read_text()
        self.assertIn("cloud_evidence.py audit", workflow)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', workflow)
        self.assertIn("cloud-evidence-cutover-audit", workflow)
        self.assertIn("steps.audit.outputs.ready", workflow)
        self.assertIn("chore(data): record cloud cutover audit", workflow)
        self.assertIn("trigger_source:", workflow)

    def test_vercel_runs_a_required_cloud_audit_each_trading_day(self):
        vercel = (ROOT / "web" / "vercel.json").read_text()
        route = (
            ROOT / "web" / "src" / "app" / "api" / "cron" / "cloud-audit" / "route.ts"
        ).read_text()

        self.assertIn('"path": "/api/cron/cloud-audit"', vercel)
        self.assertIn('"schedule": "40 7 * * 1-5"', vercel)
        self.assertIn("CLOUD_AUDIT_WORKFLOW", route)
        self.assertIn('require_ready: "true"', route)
        self.assertIn('trigger_source: "vercel-cron"', route)

    def test_intraday_workflow_installs_only_runtime_dependencies(self):
        workflow = (ROOT / ".github" / "workflows" / "intraday_scan.yml").read_text()
        self.assertIn("pip install --prefer-binary -r requirements-intraday.txt", workflow)
        self.assertNotIn("pip install -r requirements.txt", workflow)

    def test_intraday_workflow_settles_prior_session_orders_after_open(self):
        workflow = (ROOT / ".github" / "workflows" / "intraday_scan.yml").read_text()
        scan_index = workflow.index("python intraday_analysis_report.py")
        settlement_index = workflow.index("python paper_settlement.py")
        monitor_index = workflow.index("python research_monitor.py")
        persistence_index = workflow.index("bash persist_scanner_data.sh")

        self.assertIn("steps.gate.outputs.slot != '09:00'", workflow)
        self.assertIn("--source github_action --send-telegram", workflow)
        self.assertLess(scan_index, settlement_index)
        self.assertLess(settlement_index, monitor_index)
        self.assertLess(settlement_index, persistence_index)

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
        self.assertIn('"schedule": "17 0,7-23 * * 1-5"', vercel)
        self.assertIn('"schedule": "40 7 * * 1-5"', vercel)
        self.assertIn('"schedule": "20 11 * * 1-5"', vercel)
        self.assertIn('SCHEDULED_CRON: ${{ inputs.scheduled_cron }}', workflow)
        self.assertIn('INTRADAY_QUOTE_MAX_ATTEMPTS: "6"', workflow)
        self.assertIn('INTRADAY_QUOTE_RETRY_DELAY_SECONDS: "60"', workflow)

    def test_vercel_cron_routes_fail_closed_with_cron_secret(self):
        for route_name in ("intraday", "daily", "market", "institutional"):
            route = (
                ROOT / "web" / "src" / "app" / "api" / "cron" / route_name / "route.ts"
            ).read_text()
            self.assertIn("process.env.CRON_SECRET", route)
            self.assertIn("cronAuthorized", route)
            self.assertIn("process.env.GITHUB_ACTIONS_TOKEN", route)

    def test_control_center_code_changes_deploy_through_github_actions(self):
        workflow = (
            ROOT / ".github" / "workflows" / "vercel_deploy.yml"
        ).read_text()

        self.assertIn('branches:\n    - main', workflow)
        self.assertIn('- "web/**"', workflow)
        self.assertIn('- "!web/public/dashboard_snapshot.json"', workflow)
        self.assertIn("secrets.VERCEL_TOKEN", workflow)
        self.assertIn("secrets.VERCEL_ORG_ID", workflow)
        self.assertIn("secrets.VERCEL_PROJECT_ID", workflow)
        self.assertIn("vercel build --prod", workflow)
        self.assertIn("vercel deploy --prebuilt --prod", workflow)

    def test_daily_workflow_backtests_all_candidates_before_training(self):
        workflow = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()
        maturation_index = workflow.index(
            "python outcome_maturation.py --batch-size 500 --max-batches 4 --fail-on-stale"
        )
        scenario_index = workflow.index(
            "python candidate_execution_research.py --limit 1000"
        )
        training_index = workflow.index("python ai_pipeline.py --no-news")
        evaluation_index = workflow.index("python research_evaluation.py")
        challenger_index = workflow.index("python strategy_challenger.py")
        paper_index = workflow.index("python paper_trading.py")
        monitor_index = workflow.index("python research_monitor.py")
        alpha_monitor_index = workflow.index("python alpha_forward_monitor.py")
        learning_cycle_index = workflow.index("python learning_cycle.py")
        fundamental_index = workflow.index(
            "python fundamental_ingestion.py --fail-on-empty"
        )
        scanner_index = workflow.index("python scanner.py")
        challenger_factory_index = workflow.index("python challenger_factory.py")
        capital_index = workflow.index("python capital_governance.py")
        export_index = workflow.index("python export_dashboard_snapshot.py")
        self.assertIn("FORMAL_RECOMMENDATIONS_APPROVED", workflow)
        self.assertLess(fundamental_index, scanner_index)
        self.assertLess(maturation_index, training_index)
        self.assertLess(maturation_index, scenario_index)
        self.assertLess(scenario_index, training_index)
        self.assertLess(training_index, evaluation_index)
        self.assertLess(evaluation_index, challenger_index)
        self.assertLess(challenger_index, paper_index)
        self.assertLess(evaluation_index, paper_index)
        self.assertLess(training_index, paper_index)
        self.assertLess(paper_index, monitor_index)
        self.assertLess(monitor_index, export_index)
        self.assertLess(alpha_monitor_index, learning_cycle_index)
        self.assertLess(learning_cycle_index, challenger_factory_index)
        self.assertLess(challenger_factory_index, capital_index)
        self.assertLess(learning_cycle_index, capital_index)
        self.assertLess(alpha_monitor_index, capital_index)
        self.assertLess(capital_index, export_index)

    def test_intraday_rechecks_capital_governance_before_export(self):
        workflow = (
            ROOT / ".github" / "workflows" / "intraday_scan.yml"
        ).read_text()
        alpha_monitor_index = workflow.index("python alpha_forward_monitor.py")
        capital_index = workflow.index("python capital_governance.py")
        export_index = workflow.index("python export_dashboard_snapshot.py")

        self.assertIn("LIVE_CAPITAL_REFERENCE", workflow)
        self.assertLess(alpha_monitor_index, capital_index)
        self.assertLess(capital_index, export_index)

    def test_alpha_v2_model_is_governed_and_restored_before_daily_scan(self):
        research = (
            ROOT / ".github" / "workflows" / "alpha_strategy_v2.yml"
        ).read_text()
        daily = (ROOT / ".github" / "workflows" / "daily_scan.yml").read_text()

        self.assertIn("--model-output", research)
        self.assertIn("alpha_strategy_v2_model.joblib", research)
        self.assertIn("research-alpha-data-v2", research)
        restore_index = daily.index("Restore governed Alpha v2 model")
        scan_index = daily.index("python scanner.py")
        self.assertIn("alpha_strategy_v2_model.joblib", daily)
        self.assertLess(restore_index, scan_index)

    def test_research_health_workflows_propagate_manual_gate_approval(self):
        for workflow_name in (
            "daily_scan.yml",
            "intraday_scan.yml",
            "historical_replay.yml",
        ):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn("FORMAL_RECOMMENDATIONS_APPROVED", workflow)

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
        challenger_index = workflow.index("python strategy_challenger.py")
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
        self.assertLess(evaluation_index, challenger_index)
        self.assertLess(challenger_index, persistence_index)
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
            "cloud_evidence_audit.yml",
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
