from __future__ import annotations

import unittest
from unittest.mock import patch

from agent.config import AgentSettings
from agent.tools import COHORT_SCOPE_ID, TOOL_FUNCTIONS, TOOL_SCHEMAS, execute_tool
from services.workbook_data import get_encounters, get_metrics


EXPECTED_TOOLS = {
    "get_patient_timeline",
    "extract_clinical_features",
    "calculate_risk",
    "knowledge_search",
}


class WorkbookToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.encounter = get_encounters()[0]
        cls.encounter_key = cls.encounter["encounter_key"]

    def test_exactly_four_tools_are_registered(self) -> None:
        schema_names = {item["function"]["name"] for item in TOOL_SCHEMAS}
        self.assertEqual(schema_names, EXPECTED_TOOLS)
        self.assertEqual(set(TOOL_FUNCTIONS), EXPECTED_TOOLS)
        self.assertEqual(len(TOOL_SCHEMAS), 4)

    def test_example_api_key_is_not_treated_as_configured(self) -> None:
        self.assertFalse(AgentSettings(api_key="sk-替换为你的百炼Key").is_configured)

    def test_patient_scope_cannot_be_overridden(self) -> None:
        fake_result = {
            "patient_id": self.encounter_key,
            "available": True,
            "prediction": {"answer": "测试模型结果"},
            "sources": ["测试模型"],
        }
        with patch("agent.risk_model.predict_patient_risk", return_value=fake_result) as predictor:
            result = execute_tool(
                "calculate_risk",
                {"patient_id": "WRONG"},
                self.encounter_key,
            )
        predictor.assert_called_once_with(self.encounter_key)
        self.assertEqual(result["patient_id"], self.encounter_key)

    def test_timeline_reads_selected_workbook_encounter(self) -> None:
        result = execute_tool(
            "get_patient_timeline",
            {"patient_id": "WRONG"},
            self.encounter_key,
        )
        self.assertEqual(result["patient_id"], self.encounter_key)
        self.assertGreater(result["event_count"], 0)
        self.assertIn("15天窗口工作簿", result["sources"][0])

    def test_features_are_grounded_in_selected_encounter(self) -> None:
        result = execute_tool(
            "extract_clinical_features",
            {"patient_id": "WRONG"},
            self.encounter_key,
        )
        self.assertEqual(result["profile"]["regno"], self.encounter["regno"])
        self.assertEqual(result["profile"]["admno"], self.encounter["admno"])
        self.assertFalse(result["data_quality"]["risk_field_available"])

    def test_cohort_features_use_workbook_counts(self) -> None:
        result = execute_tool(
            "extract_clinical_features",
            {"patient_id": "WRONG"},
            COHORT_SCOPE_ID,
        )
        metrics = get_metrics()
        cohort = result["cohort_features"]
        self.assertEqual(cohort["patient_count"], metrics["patient_count"])
        self.assertEqual(cohort["encounter_count"], metrics["encounter_count"])
        self.assertFalse(cohort["risk_available"])

    def test_cohort_risk_prediction_is_rejected(self) -> None:
        result = execute_tool(
            "calculate_risk",
            {"patient_id": "WRONG"},
            COHORT_SCOPE_ID,
        )
        self.assertIn("仅支持", result["error"])

    def test_local_knowledge_keeps_label_and_cutoff_semantics(self) -> None:
        result = execute_tool(
            "knowledge_search",
            {"query": "label 与 cutoff_time 是风险预测吗？"},
            self.encounter_key,
        )
        body = " ".join(item["content"] for item in result["items"])
        self.assertIn("回顾性目标事件标签", body)
        self.assertIn("数据窗口截止时间", body)


if __name__ == "__main__":
    unittest.main()
