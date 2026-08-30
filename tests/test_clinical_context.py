from __future__ import annotations

import unittest

from services.clinical_context import BUCKET_ORDER, get_patient_clinical_context
from services.workbook_data import get_encounters


class ClinicalContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.encounter = None
        cls.context = None
        for encounter in get_encounters():
            context = get_patient_clinical_context(
                encounter["encounter_key"],
                use_llm_compression=False,
            )
            if context.get("event_count", 0) > 0:
                cls.encounter = encounter
                cls.context = context
                break
        if cls.encounter is None or cls.context is None:
            raise AssertionError("工作簿中未找到可用于验证时间窗的就诊记录")

    def test_context_is_one_encounter_and_removes_direct_identifiers(self) -> None:
        text = self.context["clinical_input"]
        self.assertEqual(self.context["encounter_count"], 1)
        self.assertEqual(self.context["patient_id"], self.encounter["encounter_key"])
        self.assertNotIn(self.encounter["regno"], text)
        self.assertNotIn(self.encounter["admno"], text)
        self.assertNotIn(self.encounter["encounter_key"], text)
        self.assertIn("label", self.context["excluded_fields"])

    def test_context_provenance_matches_retained_events(self) -> None:
        self.assertEqual(
            self.context["event_count"],
            len(self.context["provenance"]),
        )
        self.assertEqual(
            set(self.context["window_event_counts"]),
            set(BUCKET_ORDER),
        )
        self.assertEqual(
            sum(self.context["window_event_counts"].values()),
            self.context["event_count"],
        )
        self.assertTrue(
            all(item["bucket"] in BUCKET_ORDER for item in self.context["provenance"])
        )


if __name__ == "__main__":
    unittest.main()
