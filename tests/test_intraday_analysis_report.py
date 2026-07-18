import unittest

import pandas as pd

from intraday_analysis_report import build_report_text


class IntradayAnalysisReportTests(unittest.TestCase):
    def test_blocked_integrity_gate_downgrades_selection_language(self):
        report = build_report_text(
            pd.DataFrame(columns=["政策入選"]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            research_gate={
                "formal_recommendations_allowed": False,
                "passed_checks": 2,
                "total_checks": 11,
            },
        )
        self.assertIn("RESEARCH ONLY（2/11 項通過）", report)
        self.assertIn("研究候選（每日最多3檔", report)
        self.assertNotIn("正式模擬入選", report)
        self.assertIn("不是買進建議", report)

    def test_approved_integrity_gate_keeps_formal_simulation_label(self):
        report = build_report_text(
            pd.DataFrame(columns=["政策入選"]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            research_gate={
                "formal_recommendations_allowed": True,
                "passed_checks": 11,
                "total_checks": 11,
            },
        )
        self.assertIn("APPROVED（11/11 項通過）", report)
        self.assertIn("正式模擬入選", report)


if __name__ == "__main__":
    unittest.main()
