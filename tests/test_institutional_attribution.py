import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database import get_connection
from institutional_attribution import build_institutional_attribution
from institutional_conditional_research import (
    CONDITIONAL_SPECS,
    LOCKED_CONDITION_KEY,
    LOCKED_INTERACTION_FEATURE,
    add_locked_interaction,
    run_conditional_ablation,
    validate_attribution_report,
)
from research_evaluation import PromotionGates
from tests.test_institutional_research import _institutional_ranking_rows


class InstitutionalAttributionTests(unittest.TestCase):
    def test_discovery_report_never_uses_validation_or_holdout_labels(self):
        frame = _institutional_ranking_rows(periods=140)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.csv.gz"
            changed = root / "changed.csv.gz"
            frame.to_csv(original, index=False, compression="gzip")
            report = build_institutional_attribution(original)

            future = frame["trade_date"] > report["development_end"]
            for horizon in (5, 10):
                frame.loc[future, f"next_open_excess_return_{horizon}d"] = 9999.0
            frame.to_csv(changed, index=False, compression="gzip")
            changed_report = build_institutional_attribution(changed)

        self.assertEqual(report, changed_report)
        self.assertEqual(
            report["evaluation_scope"],
            "historical_development_discovery_only",
        )
        self.assertFalse(report["validation_evaluated"])
        self.assertFalse(report["holdout_evaluated"])
        self.assertGreater(report["reserved_validation_trade_dates"], 0)
        self.assertGreater(report["reserved_holdout_trade_dates"], 0)

    def test_locked_interaction_requires_all_pre_registered_conditions(self):
        frame = pd.DataFrame(
            {
                "foreign_net_z20": [0.5, 0.5, -0.1],
                "trust_net_z20": [0.2, 0.2, 0.2],
                "agreement_score_1d": [2.0, 1.0, 3.0],
                "industry_up_ratio": [60.0, 60.0, 70.0],
            }
        )
        result = add_locked_interaction(frame)
        self.assertEqual(result[LOCKED_INTERACTION_FEATURE].tolist(), [1, 0, 0])

    def test_conditional_research_rejects_attribution_rule_drift(self):
        report = {
            "evaluation_scope": "historical_development_discovery_only",
            "validation_evaluated": False,
            "holdout_evaluated": False,
            "selected_confirmation_candidate": {
                "flow_key": "foreign_accumulation",
                "segment_key": "industry_breadth_strong",
                "eligible_for_confirmation": True,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attribution.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pre-registered interaction"):
                validate_attribution_report(path)

    def test_conditional_ablation_stays_historical_and_shadow_only(self):
        frame = _institutional_ranking_rows(periods=100)
        gates = PromotionGates(
            min_trade_dates=5,
            min_trades=10,
            min_probabilistic_sharpe=0.0,
            max_drawdown=-100.0,
            min_profitable_fold_rate=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "institutional.csv.gz"
            database = root / "scanner.db"
            frame.to_csv(dataset, index=False, compression="gzip")
            results = run_conditional_ablation(
                dataset,
                db_path=database,
                specs=(CONDITIONAL_SPECS[0],),
                gates=gates,
                min_train_rows=30,
            )
            with get_connection(database) as conn:
                experiment = conn.execute(
                    "SELECT strategy_family FROM research_experiments "
                    "WHERE experiment_key=?",
                    (CONDITIONAL_SPECS[0].key,),
                ).fetchone()

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertFalse(result["qualified"])
        self.assertFalse(result["holdout_evaluated"])
        self.assertFalse(result["formal_ranking_enabled"])
        self.assertEqual(
            result["institutional_condition_key"], LOCKED_CONDITION_KEY
        )
        self.assertEqual(
            result["institutional_features"], [LOCKED_INTERACTION_FEATURE]
        )
        self.assertEqual(
            experiment["strategy_family"],
            "generation_2_institutional_interaction",
        )


if __name__ == "__main__":
    unittest.main()
