from __future__ import annotations

import json
import os
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_DIR = PROJECT_ROOT / "model-results"

TIME_WINDOW_LABELS = {
    "day_0": "当天",
    "day_1": "后1天",
    "day_2": "后2天",
    "day_1_2": "后1至2天",
    "day_3_14": "后3至14天",
    "no_rupture_within_14d": "未来14天内不发生",
}

RISK_LABELS = {
    "HIGH": "高风险",
    "MEDIUM": "中风险",
    "LOW": "低风险",
    "UNKNOWN": "无法判断",
}


def normalize_live_prediction(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize one real model response for display without inferring missing fields."""
    prediction = result.get("prediction") or {}
    fields = prediction.get("fields") or result
    rupture_judgment = str(fields.get("rupture_judgment") or "").strip()
    current_urgency = str(fields.get("current_urgency") or "").strip()
    core_evidence = str(
        fields.get("core_evidence") or fields.get("explanation") or ""
    ).strip()
    raw_label = str(
        fields.get("rupture_label")
        if fields.get("rupture_label") is not None
        else fields.get("predicted_label", "")
    ).strip().lower()
    raw_window = str(
        fields.get("rupture_time_window")
        or fields.get("predicted_time_window")
        or ""
    ).strip()
    confidence = str(
        fields.get("evidence_confidence")
        or fields.get("predicted_evidence_confidence")
        or ""
    ).strip()
    confidence = {
        "low": "低",
        "medium": "中",
        "high": "高",
    }.get(confidence.lower(), confidence)
    if raw_label in {"1", "true", "yes", "发生", "阳性"}:
        predicted_label: int | None = 1
    elif raw_label in {"0", "false", "no", "不发生", "阴性"}:
        predicted_label = 0
    else:
        predicted_label = None
    if rupture_judgment == "是":
        predicted_label = 1
    elif rupture_judgment == "否":
        predicted_label = 0
    elif rupture_judgment == "证据不足":
        predicted_label = None
    elif predicted_label == 1:
        rupture_judgment = "是"
    elif predicted_label == 0:
        rupture_judgment = "否"
    normalized_record = {
        "predicted_label": predicted_label,
        "predicted_evidence_confidence": confidence,
    }
    if current_urgency == "危急":
        risk_level = "HIGH"
    elif current_urgency == "暂时稳定":
        risk_level = "LOW"
    else:
        risk_level = _risk_level(normalized_record) or "UNKNOWN"
    is_available = bool(
        result.get("available", result.get("parse_ok", False))
        and rupture_judgment in {"是", "否", "证据不足"}
    )
    classification_label = (
        f"破裂判断：{rupture_judgment}"
        if rupture_judgment
        else "无法判断"
    )
    return {
        "available": is_available,
        "rupture_judgment": rupture_judgment,
        "current_urgency": current_urgency,
        "core_evidence": core_evidence,
        "risk_level": risk_level,
        "risk_label": RISK_LABELS[risk_level],
        "classification_label": classification_label,
        "predicted_label": predicted_label,
        "time_window_key": raw_window,
        "prediction_time": TIME_WINDOW_LABELS.get(
            raw_window,
            raw_window or "模型未提供具体发生时间",
        ),
        "evidence_confidence": confidence or "模型未提供",
        "explanation": core_evidence or str(result.get("model_explanation") or "").strip(),
        "answer": str(
            prediction.get("answer") or result.get("model_answer") or ""
        ).strip(),
        "model": str(result.get("model") or "").strip(),
        "duration_seconds": result.get("duration_seconds"),
        "notice": str(result.get("notice") or "").strip(),
    }


def build_live_prediction_overview(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate model calls from the current UI session for dashboard display."""
    normalized = [normalize_live_prediction(item) for item in results]
    normalized = [item for item in normalized if item["available"]]
    if not normalized:
        return {"available": False, "reason": "本次会话尚无可验证的模型预测结果。"}
    total = len(normalized)
    positive = [item for item in normalized if item["predicted_label"] == 1]
    risk_counter = Counter(item["risk_level"] for item in normalized)
    window_counter = Counter(
        item["time_window_key"]
        for item in positive
        if item["time_window_key"] in TIME_WINDOW_LABELS
    )
    review_count = sum(
        item["predicted_label"] == 1 or item["evidence_confidence"] == "低"
        for item in normalized
    )
    return {
        "available": True,
        "scope": "本次会话",
        "source_file": "当前会话模型调用",
        "total": total,
        "source_record_count": total,
        "invalid_record_count": 0,
        "predicted_positive_count": len(positive),
        "predicted_positive_rate": len(positive) / total,
        "time_window_distribution": [
            {"key": key, "label": TIME_WINDOW_LABELS[key], "count": window_counter[key]}
            for key in ("day_0", "day_1", "day_2", "day_1_2", "day_3_14")
            if window_counter[key]
        ],
        "risk_distribution": [
            {"key": key, "label": RISK_LABELS[key], "count": risk_counter[key]}
            for key in ("HIGH", "MEDIUM", "LOW")
        ],
        "review_distribution": [
            {"key": "REVIEW", "label": "建议复核", "count": review_count},
            {"key": "ROUTINE", "label": "常规随访", "count": total - review_count},
        ],
        "review_count": review_count,
        "review_rate": review_count / total,
        "risk_rule": (
            "模型二分类为“会发生”时列为高优先级复核，“未发生”时列为低优先级；"
            "如模型另有结构化证据支持度为低，则列为中优先级。该分组不是校准后的发生概率。"
        ),
        "review_rule": "二分类预测为“会发生”或结构化证据支持度低的记录，列为建议复核。",
    }


def get_prediction_path() -> Path:
    configured = os.getenv("PREDICTION_RESULTS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        from services.workbook_data import get_workbook_path

        workbook_stem = get_workbook_path().stem
        path = DEFAULT_RESULT_DIR / f"{workbook_stem}_predictions.jsonl"
    return path if path.is_absolute() else PROJECT_ROOT / path


def _prediction_signature(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    if not resolved.is_file():
        return str(resolved), 0, 0
    stat = resolved.stat()
    return str(resolved), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=4)
def _load_prediction_records(
    signature: tuple[str, int, int],
) -> dict[str, dict[str, Any]]:
    path_text, _, size = signature
    path = Path(path_text)
    if not size or not path.is_file():
        return {}
    records: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            encounter_key = str(item.get("encounter_key") or "").strip()
            if not encounter_key or not item.get("parse_ok"):
                continue
            records[encounter_key] = item
    return records


def get_prediction_records_by_encounter() -> dict[str, dict[str, Any]]:
    """Return persisted, parseable model results keyed by regno + admno."""
    path = get_prediction_path()
    return deepcopy(_load_prediction_records(_prediction_signature(path)))


def get_prediction_for_encounter(encounter_key: str) -> dict[str, Any] | None:
    return get_prediction_records_by_encounter().get(str(encounter_key or "").strip())


def _risk_level(record: dict[str, Any]) -> str | None:
    """Convert model output to a transparent three-level ordinal group."""
    label = record.get("predicted_label")
    confidence = record.get("predicted_evidence_confidence")
    if label not in (0, 1):
        return None
    if confidence not in {"低", "中", "高"}:
        return "HIGH" if label == 1 else "LOW"
    if confidence == "低":
        return "MEDIUM"
    return "HIGH" if label == 1 else "LOW"


def _needs_review(record: dict[str, Any]) -> bool:
    return (
        record.get("predicted_label") == 1
        or record.get("predicted_evidence_confidence") == "低"
    )


@lru_cache(maxsize=4)
def _load_prediction_overview(signature: tuple[str, int, int]) -> dict[str, Any]:
    path_text, _, size = signature
    path = Path(path_text)
    if not size or not path.is_file():
        return {
            "available": False,
            "reason": "尚未找到模型预测结果，无法计算科室研判分布。",
            "path": str(path),
        }

    records: list[dict[str, Any]] = []
    invalid_lines = 0
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(item, dict):
                records.append(item)
            else:
                invalid_lines += 1

    if not records:
        return {
            "available": False,
            "reason": "模型预测结果为空或无法解析，暂不能计算分布。",
            "path": str(path),
        }

    parsed = [
        record
        for record in records
        if record.get("parse_ok")
        and record.get("predicted_label") in (0, 1)
    ]
    positive = [record for record in parsed if record["predicted_label"] == 1]

    window_counter = Counter(record["predicted_time_window"] for record in positive)
    risk_counter = Counter(
        level for record in parsed if (level := _risk_level(record)) is not None
    )
    review_count = sum(_needs_review(record) for record in parsed)
    total = len(parsed)
    source_workbooks = {
        str(record.get("source_workbook") or "").strip()
        for record in parsed
        if str(record.get("source_workbook") or "").strip()
    }

    return {
        "available": True,
        "scope": "当前工作簿批量预测" if source_workbooks else "模型结果文件",
        "source_file": path.name,
        "total": total,
        "source_record_count": len(records),
        "invalid_record_count": len(records) - total + invalid_lines,
        "predicted_positive_count": len(positive),
        "predicted_positive_rate": len(positive) / total if total else 0.0,
        "time_window_distribution": [
            {"key": key, "label": TIME_WINDOW_LABELS[key], "count": window_counter[key]}
            for key in ("day_0", "day_1", "day_2", "day_1_2", "day_3_14")
            if window_counter[key]
        ],
        "risk_distribution": [
            {"key": key, "label": label, "count": risk_counter[key]}
            for key, label in (("HIGH", "高风险"), ("MEDIUM", "中风险"), ("LOW", "低风险"))
        ],
        "review_distribution": [
            {"key": "REVIEW", "label": "建议复核", "count": review_count},
            {"key": "ROUTINE", "label": "常规随访", "count": total - review_count},
        ],
        "review_count": review_count,
        "review_rate": review_count / total if total else 0.0,
        "risk_rule": (
            "模型二分类为“会发生”时列为高优先级复核，“未发生”时列为低优先级；"
            "如模型另有结构化证据支持度为低，则列为中优先级。该分组不是校准后的发生概率。"
        ),
        "review_rule": "二分类预测为“会发生”或结构化证据支持度低的记录，列为建议复核。",
    }


def get_prediction_overview() -> dict[str, Any]:
    path = get_prediction_path()
    return deepcopy(_load_prediction_overview(_prediction_signature(path)))
