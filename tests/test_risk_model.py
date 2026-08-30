from __future__ import annotations

import unittest
from unittest.mock import patch

from agent.risk_model import parse_risk_output
from agent.tools import execute_tool


class RiskOutputParserTests(unittest.TestCase):
    def test_uploaded_model_binary_conclusion_is_parsed(self) -> None:
        result = parse_risk_output(
            "【预测结论】 未发生心脏破裂\n\n现有资料支持病情相对稳定。"
        )
        self.assertEqual(result["fields"]["rupture_label"], "0")
        self.assertIn("相对稳定", result["fields"]["explanation"])

    def test_think_and_answer_are_separated(self) -> None:
        result = parse_risk_output(
            "<think>关键证据与不确定性。</think>"
            "<answer>rupture_label: 1\n"
            "rupture_time_window: day_1_2\n"
            "evidence_confidence: 中</answer>"
        )
        self.assertEqual(result["thinking"], "关键证据与不确定性。")
        self.assertTrue(result["has_think_tag"])
        self.assertTrue(result["has_answer_tag"])
        self.assertEqual(result["fields"]["rupture_label"], "1")
        self.assertEqual(result["fields"]["rupture_time_window"], "day_1_2")

    def test_execute_tool_keeps_ui_selected_patient_scope(self) -> None:
        fake_result = {
            "patient_id": "XD-SELECTED",
            "available": True,
            "prediction": {"thinking": "", "answer": "合成结果"},
            "sources": ["合成测试"],
        }
        with patch(
            "agent.risk_model.predict_patient_risk",
            return_value=fake_result,
        ) as predictor:
            result = execute_tool(
                "calculate_risk",
                {"patient_id": "XD-MODEL-OVERRIDE"},
                "XD-SELECTED",
            )

        predictor.assert_called_once_with("XD-SELECTED")
        self.assertEqual(result["patient_id"], "XD-SELECTED")


if __name__ == "__main__":
    unittest.main()
