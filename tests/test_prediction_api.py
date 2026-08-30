from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.prediction_api import _load_prediction_overview, normalize_live_prediction


class PredictionOverviewTests(unittest.TestCase):
    def test_live_model_result_is_normalized_without_inference(self) -> None:
        result = normalize_live_prediction(
            {
                "available": True,
                "model": "cardiac-rupture-qwen38",
                "prediction": {
                    "fields": {
                        "rupture_label": "1",
                        "rupture_time_window": "day_1_2",
                        "evidence_confidence": "高",
                        "explanation": "仅用于测试解析",
                    }
                },
            }
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["risk_label"], "高风险")
        self.assertEqual(result["prediction_time"], "后1至2天")

    def test_incomplete_live_result_stays_unknown(self) -> None:
        result = normalize_live_prediction(
            {"available": True, "prediction": {"fields": {}}}
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["risk_level"], "UNKNOWN")

    def test_predictions_are_aggregated_without_retrospective_label_leakage(self) -> None:
        records = [
            {
                "label": 0,
                "predicted_label": 1,
                "predicted_time_window": "day_1_2",
                "predicted_evidence_confidence": "高",
                "parse_ok": True,
            },
            {
                "label": 1,
                "predicted_label": 0,
                "predicted_time_window": "no_rupture_within_14d",
                "predicted_evidence_confidence": "高",
                "parse_ok": True,
            },
            {
                "label": 1,
                "predicted_label": 0,
                "predicted_time_window": "no_rupture_within_14d",
                "predicted_evidence_confidence": "低",
                "parse_ok": True,
            },
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.jsonl"
            path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
                encoding="utf-8",
            )
            stat = path.stat()
            result = _load_prediction_overview((str(path), stat.st_mtime_ns, stat.st_size))

        self.assertTrue(result["available"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["predicted_positive_count"], 1)
        self.assertEqual(result["review_count"], 2)
        self.assertEqual(
            {row["key"]: row["count"] for row in result["risk_distribution"]},
            {"HIGH": 1, "MEDIUM": 1, "LOW": 1},
        )


if __name__ == "__main__":
    unittest.main()
