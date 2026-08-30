from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    payload = payload | {"updated_at": datetime.now().isoformat(timespec="seconds")}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _completed_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    completed: set[str] = set()
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(item.get("encounter_key") or "").strip()
            if key and item.get("parse_ok"):
                completed.add(key)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description="批量运行心脏破裂二分类模型")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    os.environ["PATIENT_WORKBOOK_XLSX"] = str(Path(args.workbook).resolve())
    result_path = Path(args.result).resolve()
    status_path = Path(args.status).resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)

    from agent.risk_model import CardiacRuptureRiskModel
    from services.clinical_context import get_patient_clinical_context
    from services.prediction_api import normalize_live_prediction
    from services.workbook_data import get_encounters, get_source_name

    encounters = get_encounters()
    if args.limit is not None:
        encounters = encounters[: max(0, args.limit)]
    completed_keys = _completed_keys(result_path)
    selected_keys = {item["encounter_key"] for item in encounters}
    completed = len(selected_keys & completed_keys)
    failed = 0
    consecutive_failures = 0
    total = len(encounters)
    model = CardiacRuptureRiskModel()

    _write_status(
        status_path,
        {
            "state": "running",
            "total": total,
            "completed": completed,
            "failed": failed,
            "message": f"正在预测 {get_source_name()}，支持中断后续跑。",
        },
    )

    with result_path.open("a", encoding="utf-8") as output:
        for encounter in encounters:
            encounter_key = encounter["encounter_key"]
            if encounter_key in completed_keys:
                continue
            try:
                context = get_patient_clinical_context(
                    encounter_key,
                    use_llm_compression=False,
                )
                if context.get("error"):
                    raise RuntimeError(str(context["error"]))
                raw_result = model.predict_with_context(encounter_key, context)
                normalized = normalize_live_prediction(raw_result)
                if not normalized["available"]:
                    raise RuntimeError(str(raw_result.get("error") or "模型结论无法解析"))
                record = {
                    "encounter_key": encounter_key,
                    "parse_ok": True,
                    "predicted_label": normalized["predicted_label"],
                    "predicted_time_window": normalized["time_window_key"],
                    "predicted_evidence_confidence": (
                        "" if normalized["evidence_confidence"] == "模型未提供" else normalized["evidence_confidence"]
                    ),
                    "model_explanation": normalized["explanation"],
                    "model_answer": normalized["answer"],
                    "model": raw_result.get("model"),
                    "duration_seconds": raw_result.get("duration_seconds"),
                    "source_workbook": get_source_name(),
                }
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                completed += 1
                completed_keys.add(encounter_key)
                consecutive_failures = 0
            except Exception as exc:
                failed += 1
                consecutive_failures += 1
                last_error = f"{type(exc).__name__}: {exc}"

            _write_status(
                status_path,
                {
                    "state": "running",
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "message": "批量预测进行中。",
                },
            )
            if consecutive_failures >= 10:
                _write_status(
                    status_path,
                    {
                        "state": "failed",
                        "total": total,
                        "completed": completed,
                        "failed": failed,
                        "message": f"连续10条预测失败，任务已停止：{last_error}",
                    },
                )
                return 2

    _write_status(
        status_path,
        {
            "state": "completed",
            "total": total,
            "completed": completed,
            "failed": failed,
            "message": "当前选择范围的批量预测已完成。",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
