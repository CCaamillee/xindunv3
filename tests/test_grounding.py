from __future__ import annotations

import unittest

from services.workbook_data import (
    get_encounter_detail,
    get_encounter_dataframe,
    get_metrics,
    get_patient_encounters,
    get_patient_prediction_context,
)
from agent.react_agent import _sanitize_tool_result_for_model


class WorkbookGroundingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = get_encounter_dataframe()
        cls.first_key = str(cls.frame.iloc[0]["encounter_key"])

    def test_workbook_baseline_counts(self) -> None:
        metrics = get_metrics()
        self.assertEqual(metrics["patient_count"], 5348)
        self.assertEqual(metrics["encounter_count"], 14419)
        self.assertEqual(metrics["multi_visit_patient_count"], 5063)
        self.assertEqual(metrics["target_event_count"], 0)
        self.assertEqual(metrics["surgery_record_count"], 1807)

    def test_missing_model_fields_are_not_fabricated(self) -> None:
        self.assertEqual(set(self.frame["risk_level"]), {"UNKNOWN"})
        self.assertEqual(set(self.frame["risk_label"]), {"无法判断"})
        self.assertEqual(set(self.frame["prediction_time"]), {"暂无模型结果"})
        detail = get_encounter_detail(self.first_key)
        risk_text = " ".join(item["value"] for item in detail["risk"])
        self.assertIn("label=0", risk_text)
        self.assertIn("cutoff_time 仅作为数据窗口截止时间", risk_text)

    def test_multiple_visits_are_kept_separate(self) -> None:
        counts = self.frame.groupby("regno")["admno"].nunique()
        regno = str(counts[counts > 1].index[0])
        visits = get_patient_encounters(regno)
        self.assertGreater(len(visits), 1)
        self.assertEqual(len({item["admno"] for item in visits}), len(visits))
        for visit in visits:
            detail = get_encounter_detail(visit["encounter_key"])
            self.assertEqual(detail["profile"]["regno"], regno)
            self.assertEqual(detail["profile"]["admno"], visit["admno"])

    def test_timeline_uses_real_sorted_datetimes_and_sources(self) -> None:
        detail = get_encounter_detail(self.first_key)
        timeline = detail["timeline"]
        self.assertTrue(timeline)
        datetimes = [event["datetime"] for event in timeline]
        self.assertEqual(datetimes, sorted(datetimes))
        self.assertTrue(all(event["source_field"] for event in timeline))
        self.assertTrue(any(event["source_field"] == "cutoff_time" for event in timeline))
        self.assertFalse(any(event["type"] == "模型预测" for event in timeline))

    def test_prediction_context_excludes_direct_ids_and_label(self) -> None:
        context = get_patient_prediction_context(self.first_key)
        self.assertNotIn(self.first_key, context["clinical_text"])
        self.assertNotIn("regno", context["clinical_text"].lower())
        self.assertNotIn("admno", context["clinical_text"].lower())
        self.assertNotIn("label=", context["clinical_text"].lower())
        self.assertEqual(
            context["excluded_fields"],
            ["regno", "admno", "label", "cutoff_time 后信息"],
        )

    def test_agent_observation_removes_stable_identifiers(self) -> None:
        sanitized = _sanitize_tool_result_for_model(
            {
                "patient_id": self.first_key,
                "profile": {
                    "regno": "R-TEST",
                    "admno": "A-TEST",
                    "diagnosis": "保留诊断",
                },
            }
        )
        self.assertNotIn("patient_id", sanitized)
        self.assertNotIn("regno", sanitized["profile"])
        self.assertNotIn("admno", sanitized["profile"])
        self.assertEqual(sanitized["profile"]["diagnosis"], "保留诊断")


if __name__ == "__main__":
    unittest.main()
