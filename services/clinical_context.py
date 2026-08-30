from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

from agent.config import (
    context_compression_enabled,
    get_agent_settings,
    get_context_model,
    get_context_timeout_seconds,
)
from services.workbook_data import (
    get_encounter_source_record,
    get_source_name,
    workbook_signature,
)


BUCKET_ORDER = ("recent_0_1d", "day_2", "day_3_14")
BUCKET_LABELS = {
    "recent_0_1d": "近0～1天",
    "day_2": "2天前",
    "day_3_14": "3～14天前",
}
MODULE_ORDER = (
    "symptoms",
    "circulation",
    "laboratory",
    "cardiac_imaging",
    "infarction_reperfusion",
    "course",
)
MODULE_LABELS = {
    "symptoms": "症状及变化",
    "circulation": "循环和生命体征",
    "laboratory": "检验",
    "cardiac_imaging": "心脏影像",
    "infarction_reperfusion": "心肌梗死与再灌注",
    "course": "病程与治疗变化",
}
EXCLUDED_FIELDS = (
    "regno",
    "admno",
    "label",
    "回顾性目标事件",
    "预测截点后结局",
    "绝对日期",
)
MAX_CLINICAL_INPUT_CHARS = 6000


TEXT_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "time": "门诊-记录时间",
        "module": "symptoms",
        "event_type": "symptom",
        "source": "门诊记录",
        "fields": (
            ("门诊-主诉", "主诉"),
            ("门诊-现病史", "现病史"),
            ("门诊-体格检查", "体格检查"),
            ("门诊-病情变化及处置", "病情变化"),
        ),
    },
    {
        "time": "首页入院时间",
        "module": "symptoms",
        "event_type": "symptom",
        "source": "入院记录",
        "fields": (
            ("主诉", "主诉"),
            ("现病史", "现病史"),
            ("体格检查(生命体征、一般情况)", "体格检查"),
            ("专科检查", "专科检查"),
            ("入院诊断", "入院诊断"),
        ),
    },
    {
        "time": "急诊-就诊时间",
        "module": "symptoms",
        "event_type": "symptom",
        "source": "急诊记录",
        "fields": (
            ("急诊-主要就诊原因", "主要就诊原因"),
            ("急诊-主诊断名称", "急诊主诊断"),
        ),
    },
    {
        "time": "日常病程记录时间",
        "module": "course",
        "event_type": "course",
        "source": "日常病程",
        "fields": (("日常病程", "病程记录"),),
    },
    {
        "time": "上级查房时间",
        "module": "course",
        "event_type": "course",
        "source": "上级查房",
        "fields": (("上级查房记录", "查房记录"),),
    },
    {
        "time": "记录时间",
        "module": "course",
        "event_type": "course",
        "source": "病例摘要",
        "fields": (
            ("病例特点", "病例特点"),
            ("初步诊断", "初步诊断"),
            ("诊断依据", "诊断依据"),
        ),
    },
    {
        "time": "检查日期",
        "module": "cardiac_imaging",
        "event_type": "imaging",
        "source": "检查记录",
        "fields": (
            ("检查名称", "检查"),
            ("检查所见", "检查所见"),
            ("检查结果", "检查结果"),
        ),
    },
    {
        "time": "超声-检查时间",
        "module": "cardiac_imaging",
        "event_type": "echo",
        "source": "心脏超声",
        "fields": (
            ("超声-检查名称", "超声检查"),
            ("超声-射血分数", "LVEF"),
            ("超声-超声描述", "超声描述"),
            ("超声-超声提示", "超声提示"),
        ),
    },
    {
        "time": "介入-手术日期",
        "module": "infarction_reperfusion",
        "event_type": "reperfusion_pci",
        "source": "介入手术",
        "fields": (
            ("介入-术前诊断", "术前诊断"),
            ("介入-手术名称", "手术名称"),
            ("介入-本次手术", "介入过程"),
            ("介入-结论", "介入结论"),
        ),
    },
    {
        "time": "诊断时间",
        "module": "course",
        "event_type": "diagnosis",
        "source": "诊断记录",
        "fields": (("诊断名称", "诊断"), ("诊断备注", "诊断备注")),
    },
    {
        "time": "用药开始时间",
        "module": "course",
        "event_type": "treatment",
        "source": "用药记录",
        "fields": (
            ("药品通用名称", "药品"),
            ("用药方式", "用药方式"),
            ("单次剂量", "单次剂量"),
            ("药物剂量单位", "剂量单位"),
        ),
    },
)


COMPRESSION_SYSTEM_PROMPT = """
你是临床记录压缩器，只能压缩和去重输入中已存在的事实。
不得推断诊断、因果、风险、未来结局或治疗决策；不得增加症状、数字、单位、时间或阴性结果。
每段结果必须引用同一观察窗中的 evidence_ids。只输出 JSON，不输出 Markdown。
""".strip()


def _split_raw(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "nat"}:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def _safe_text(value: object, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "nan", "none", "null", "nat"}:
        return ""
    text = re.sub(
        r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b",
        "[日期已转为相对时间]",
        text,
    )
    text = re.sub(
        r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日",
        "[日期已转为相对时间]",
        text,
    )
    return text[:limit].strip()


def _parse_event_time(value: object) -> tuple[datetime | None, str]:
    text = str(value or "").strip().replace("/", "-").replace("T", " ")
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return None, "unknown"
    date_only = bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text))
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d%H%M%S",
        "%Y%m%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed, "date" if date_only or fmt in {"%Y-%m-%d", "%Y%m%d"} else "datetime"
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed, "date" if date_only else "datetime"
    except ValueError:
        return None, "unknown"


def _quality(
    flags: list[dict[str, Any]],
    code: str,
    message: str,
    columns: Iterable[str] = (),
) -> None:
    flags.append(
        {
            "code": code,
            "message": message,
            "source_columns": sorted({str(column) for column in columns if column}),
        }
    )


def _summarize_quality(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for flag in flags:
        key = (flag["code"], flag["message"])
        item = grouped.setdefault(
            key,
            {
                "code": flag["code"],
                "message": flag["message"],
                "count": 0,
                "source_columns": set(),
            },
        )
        item["count"] += 1
        item["source_columns"].update(flag.get("source_columns", []))
    return [
        item | {"source_columns": sorted(item["source_columns"])}
        for item in grouped.values()
    ]


def _explode_text_group(
    row: dict[str, Any],
    spec: dict[str, Any],
    flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    time_column = str(spec["time"])
    time_values = _split_raw(row.get(time_column))
    populated = [
        (column, label, _split_raw(row.get(column)))
        for column, label in spec["fields"]
        if _split_raw(row.get(column))
    ]
    if not populated:
        return []
    if not time_values:
        _quality(
            flags,
            "missing_event_time",
            "临床文本缺少可靠时间，未纳入动态观察窗。",
            [time_column, *(column for column, _, _ in populated)],
        )
        return []
    aligned: dict[str, tuple[str, list[str]]] = {}
    for column, label, values in populated:
        if len(values) == len(time_values):
            aligned[column] = (label, values)
        elif len(values) == 1 and len(time_values) == 1:
            aligned[column] = (label, values)
        else:
            _quality(
                flags,
                "alignment_mismatch",
                f"时间{len(time_values)}项与{label}{len(values)}项无法可靠对齐，该字段未纳入。",
                [time_column, column],
            )
    events: list[dict[str, Any]] = []
    for index, time_value in enumerate(time_values):
        event_time, precision = _parse_event_time(time_value)
        if event_time is None:
            _quality(flags, "invalid_event_time", "时间无法解析，对应事件未纳入。", [time_column])
            continue
        pieces: list[str] = []
        columns = [time_column]
        for column, (label, values) in aligned.items():
            text = _safe_text(values[index])
            if text:
                pieces.append(f"{label}：{text}")
                columns.append(column)
        if pieces:
            events.append(
                {
                    "event_time": event_time,
                    "time_precision": precision,
                    "source_module": spec["source"],
                    "event_type": spec["event_type"],
                    "module": spec["module"],
                    "content": "；".join(pieces),
                    "source_columns": columns,
                    "item_name": "",
                    "value": "",
                    "unit": "",
                }
            )
    return events


def _aligned_optional(
    values: list[str],
    count: int,
    label: str,
    columns: Iterable[str],
    flags: list[dict[str, Any]],
) -> list[str]:
    if not values:
        return [""] * count
    if len(values) == count:
        return values
    _quality(
        flags,
        "alignment_mismatch",
        f"{label}{len(values)}项与项目{count}项无法可靠对齐，未使用该元数据。",
        columns,
    )
    return [""] * count


def _explode_measurements(
    row: dict[str, Any],
    *,
    time_column: str,
    name_column: str,
    value_column: str,
    unit_column: str | None,
    flag_column: str | None,
    reference_column: str | None,
    module: str,
    event_type: str,
    source_module: str,
    flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    names = _split_raw(row.get(name_column))
    values = _split_raw(row.get(value_column))
    times = _split_raw(row.get(time_column))
    if not names and not values:
        return []
    if not names or len(names) != len(values):
        _quality(
            flags,
            "alignment_mismatch",
            f"项目{len(names)}项与结果{len(values)}项无法可靠对齐，该组记录未纳入。",
            [name_column, value_column],
        )
        return []
    count = len(names)
    if len(times) == 1:
        times = times * count
    elif len(times) != count:
        _quality(
            flags,
            "alignment_mismatch",
            f"时间{len(times)}项与项目{count}项无法可靠对齐，该组记录未纳入。",
            [time_column, name_column, value_column],
        )
        return []
    units = _split_raw(row.get(unit_column)) if unit_column else []
    if units and len(units) != count:
        _quality(
            flags,
            "alignment_mismatch",
            f"单位{len(units)}项与项目{count}项无法可靠对齐，该组记录未纳入。",
            [name_column, value_column, unit_column or ""],
        )
        return []
    units = units or [""] * count
    abnormal = _aligned_optional(
        _split_raw(row.get(flag_column)) if flag_column else [],
        count,
        "异常标记",
        [flag_column or ""],
        flags,
    )
    references = _aligned_optional(
        _split_raw(row.get(reference_column)) if reference_column else [],
        count,
        "参考范围",
        [reference_column or ""],
        flags,
    )
    events: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        event_time, precision = _parse_event_time(times[index])
        if event_time is None:
            _quality(flags, "invalid_event_time", "时间无法解析，对应项目未纳入。", [time_column])
            continue
        clean_name = _safe_text(name, 100)
        clean_value = _safe_text(values[index], 80)
        clean_unit = _safe_text(units[index], 40)
        content = f"{clean_name}：{clean_value}{(' ' + clean_unit) if clean_unit else ''}"
        if abnormal[index]:
            content += f"，异常标记：{_safe_text(abnormal[index], 30)}"
        if references[index]:
            content += f"，参考范围：{_safe_text(references[index], 80)}"
        events.append(
            {
                "event_time": event_time,
                "time_precision": precision,
                "source_module": source_module,
                "event_type": event_type,
                "module": module,
                "content": content,
                "source_columns": [
                    column
                    for column in (
                        time_column,
                        name_column,
                        value_column,
                        unit_column,
                        flag_column,
                        reference_column,
                    )
                    if column
                ],
                "item_name": clean_name,
                "value": clean_value,
                "unit": clean_unit,
            }
        )
    return events


def explode_encounter_events(
    row: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for spec in TEXT_GROUPS:
        events.extend(_explode_text_group(row, spec, flags))
    events.extend(
        _explode_measurements(
            row,
            time_column="测量时间",
            name_column="项目名称",
            value_column="测量值",
            unit_column=None,
            flag_column=None,
            reference_column=None,
            module="circulation",
            event_type="vital",
            source_module="护理测量",
            flags=flags,
        )
    )
    for suffix in ("", "_2", "_3"):
        events.extend(
            _explode_measurements(
                row,
                time_column=f"采集时间{suffix}",
                name_column=f"检验项名称{suffix}",
                value_column=f"检验项值{suffix}",
                unit_column=f"单位{suffix}",
                flag_column=f"异常提示{suffix}",
                reference_column=f"正常值范围{suffix}",
                module="laboratory",
                event_type="lab",
                source_module="检验结果",
                flags=flags,
            )
        )
    return events, flags


def assign_observation_bucket(
    event_time: datetime,
    time_precision: str,
    as_of_time: datetime,
    as_of_precision: str = "datetime",
) -> str | None:
    if time_precision == "date" or as_of_precision == "date":
        days_before = (as_of_time.date() - event_time.date()).days
        if 0 <= days_before <= 1:
            return "recent_0_1d"
        if days_before == 2:
            return "day_2"
        if 3 <= days_before <= 14:
            return "day_3_14"
        return None
    hours_before = (as_of_time - event_time).total_seconds() / 3600
    if 0 <= hours_before < 48:
        return "recent_0_1d"
    if 48 <= hours_before < 72:
        return "day_2"
    if 72 <= hours_before <= 360:
        return "day_3_14"
    return None


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[datetime, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: item["event_time"]):
        normalized = re.sub(r"[\W_]+", "", event["content"].lower())
        key = (
            event["event_time"],
            event.get("time_precision", "unknown"),
            event["module"],
            normalized,
        )
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def _numeric(value: str) -> float | None:
    match = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*", str(value or ""))
    return float(match.group(1)) if match else None


def _objective_trends(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("item_name") and _numeric(event.get("value", "")) is not None:
            groups[(event["item_name"], event.get("unit", ""))].append(event)
    trends: list[dict[str, Any]] = []
    for (item_name, unit), rows in groups.items():
        ordered = sorted(rows, key=lambda item: item["event_time"])
        if len(ordered) < 2 or ordered[0]["value"] == ordered[-1]["value"]:
            continue
        suffix = f" {unit}" if unit else ""
        trends.append(
            {
                "text": f"{item_name}由{ordered[0]['value']}{suffix}变化至{ordered[-1]['value']}{suffix}",
                "event_ids": [ordered[0]["fact_id"], ordered[-1]["fact_id"]],
            }
        )
    return trends[:12]


def _facts_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    windows: dict[str, Any] = {
        key: {
            "label": BUCKET_LABELS[key],
            "event_count": 0,
            "modules": {module: [] for module in MODULE_ORDER},
        }
        for key in BUCKET_ORDER
    }
    for event in events:
        bucket = event["bucket"]
        windows[bucket]["event_count"] += 1
        windows[bucket]["modules"][event["module"]].append(
            {"evidence_id": event["fact_id"], "text": event["content"]}
        )
    return windows


def _deterministic_render(windows: dict[str, Any], trends: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for bucket in BUCKET_ORDER:
        lines: list[str] = []
        for module in MODULE_ORDER:
            facts = windows[bucket]["modules"].get(module, [])
            if facts:
                lines.append(
                    f"{MODULE_LABELS[module]}："
                    + "；".join(item["text"] for item in facts)
                )
        if lines:
            sections.append(f"【{BUCKET_LABELS[bucket]}】\n" + "\n".join(lines))
    if trends:
        sections.append("【总体变化】\n" + "；".join(item["text"] for item in trends))
    return "\n\n".join(sections) or "预测截点前0～14天内未获得可靠对齐的临床事件。"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_compression(
    compressed: dict[str, Any],
    windows: dict[str, Any],
    trends: list[dict[str, Any]],
) -> bool:
    evidence_text: dict[str, str] = {}
    evidence_bucket: dict[str, str] = {}
    for bucket in BUCKET_ORDER:
        for module in MODULE_ORDER:
            for fact in windows[bucket]["modules"].get(module, []):
                evidence_text[fact["evidence_id"]] = fact["text"]
                evidence_bucket[fact["evidence_id"]] = bucket
    for trend in trends:
        evidence_text["T-" + "-".join(trend["event_ids"])] = trend["text"]
    forbidden = ("将发生心脏破裂", "即将发生", "高风险", "考虑心脏破裂", "提示心脏破裂")
    for key in (*BUCKET_ORDER, "overall_change"):
        item = compressed.get(key)
        if not isinstance(item, dict):
            return False
        text = str(item.get("text") or "").strip()
        ids = item.get("evidence_ids") or []
        if not isinstance(ids, list) or (text and not ids):
            return False
        cited: list[str] = []
        for evidence_id in ids:
            evidence_id = str(evidence_id)
            if evidence_id not in evidence_text:
                return False
            if key in BUCKET_ORDER and evidence_bucket.get(evidence_id) != key:
                return False
            cited.append(evidence_text[evidence_id])
        source_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(cited)))
        output_numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
        if not output_numbers.issubset(source_numbers) or any(term in text for term in forbidden):
            return False
    return True


def _compress_with_qwen(
    windows: dict[str, Any],
    trends: list[dict[str, Any]],
    client: Any | None = None,
) -> tuple[dict[str, Any] | None, str]:
    settings = get_agent_settings()
    if not context_compression_enabled() or not settings.is_configured:
        return None, "deterministic_fallback"
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=get_context_timeout_seconds(),
            max_retries=0,
        )
    payload = {
        "windows": windows,
        "objective_trends": trends,
        "required_output": {
            key: {"text": "压缩后的事实，无记录则为空字符串", "evidence_ids": ["使用的事实ID"]}
            for key in (*BUCKET_ORDER, "overall_change")
        },
    }
    try:
        response = client.chat.completions.create(
            model=get_context_model(),
            messages=[
                {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=3000,
            extra_body={"enable_thinking": False},
        )
        parsed = _parse_json_object(response.choices[0].message.content or "")
    except Exception:
        return None, "compression_failed"
    if parsed is None or not _validate_compression(parsed, windows, trends):
        return None, "validation_failed"
    return parsed, "compressed"


def _compressed_render(compressed: dict[str, Any]) -> str:
    sections: list[str] = []
    for bucket in BUCKET_ORDER:
        text = str(compressed[bucket].get("text") or "").strip()
        if text:
            sections.append(f"【{BUCKET_LABELS[bucket]}】\n{text}")
    overall = str(compressed["overall_change"].get("text") or "").strip()
    if overall:
        sections.append(f"【总体变化】\n{overall}")
    return "\n\n".join(sections) or "预测截点前0～14天内未获得可靠对齐的临床事件。"


def _build_context(
    encounter_key: str,
    as_of_time: str | datetime | None = None,
    *,
    use_llm_compression: bool = True,
    compressor_client: Any | None = None,
) -> dict[str, Any]:
    row = get_encounter_source_record(encounter_key)
    cutoff_raw: object = as_of_time if as_of_time is not None else row.get("cutoff_time")
    if isinstance(cutoff_raw, datetime):
        cutoff, cutoff_precision = cutoff_raw, "datetime"
    else:
        cutoff, cutoff_precision = _parse_event_time(cutoff_raw)
    if cutoff is None:
        return {
            "patient_id": encounter_key,
            "error": "当前就诊缺少可靠的 cutoff_time，无法构建预测截点前观察窗。",
            "quality_flags": [
                {
                    "code": "missing_as_of_time",
                    "message": "预测截点缺失",
                    "count": 1,
                    "source_columns": ["cutoff_time"],
                }
            ],
            "sources": [get_source_name()],
        }
    events, flags = explode_encounter_events(row)
    retained: list[dict[str, Any]] = []
    future_count = 0
    outside_count = 0
    for event in events:
        is_future = (
            event["event_time"].date() > cutoff.date()
            if event["time_precision"] == "date" or cutoff_precision == "date"
            else event["event_time"] > cutoff
        )
        if is_future:
            future_count += 1
            continue
        bucket = assign_observation_bucket(
            event["event_time"],
            event["time_precision"],
            cutoff,
            cutoff_precision,
        )
        if bucket is None:
            outside_count += 1
            continue
        retained.append(event | {"bucket": bucket})
    if future_count:
        _quality(flags, "future_event_excluded", f"已排除{future_count}条预测截点后事件。")
    if outside_count:
        _quality(flags, "outside_observation_window", f"已排除{outside_count}条观察窗外事件。")
    retained = _deduplicate_events(retained)
    for index, event in enumerate(retained, 1):
        event["fact_id"] = f"F{index:04d}"
    windows = _facts_payload(retained)
    trends = _objective_trends(retained)
    deterministic = _deterministic_render(windows, trends)
    compressed: dict[str, Any] | None = None
    compression_status = "disabled"
    if use_llm_compression:
        compressed, compression_status = _compress_with_qwen(
            windows,
            trends,
            client=compressor_client,
        )
    window_text = _compressed_render(compressed) if compressed else deterministic
    if compression_status in {"compression_failed", "validation_failed"}:
        _quality(flags, compression_status, "文本压缩未通过，已自动使用确定性模板。")
    age = _safe_text(row.get("年龄"), 20) or "未知"
    gender = _safe_text(row.get("性别"), 10) or "未知"
    department = _safe_text(row.get("诊断科室"), 60) or "未知"
    header = (
        "以下为当前预测截点前的脱敏临床事实。"
        "近0～1天、2天前、3～14天前均为过去观察窗，不是未来预测时间窗。"
        "未获得可靠记录表示未知，不等于正常或阴性。\n\n"
        f"【基本资料】\n年龄：{age}；性别：{gender}；来源科室：{department}"
    )
    clinical_input = header + "\n\n" + window_text
    if len(clinical_input) > MAX_CLINICAL_INPUT_CHARS:
        clinical_input = (
            clinical_input[:MAX_CLINICAL_INPUT_CHARS].rstrip()
            + "\n\n【资料截断说明】其余较早或重复内容因模型输入长度限制未发送。"
        )
        _quality(
            flags,
            "model_input_truncated",
            "临床上下文超过模型输入上限，已优先保留较近观察窗内容。",
        )
    for identifier in (row.get("regno"), row.get("admno"), encounter_key):
        text = str(identifier or "").strip()
        if text:
            clinical_input = clinical_input.replace(text, "[标识已移除]")
    provenance = [
        {
            "evidence_id": event["fact_id"],
            "bucket": event["bucket"],
            "module": event["module"],
            "time_precision": event["time_precision"],
            "source_module": event["source_module"],
            "source_columns": event["source_columns"],
        }
        for event in retained
    ]
    return {
        "patient_id": encounter_key,
        "as_of": "当前就诊 cutoff_time",
        "observation_window": "预测截点前0～360小时",
        "window_definitions": {
            "recent_0_1d": "截点前0～48小时",
            "day_2": "截点前48～72小时",
            "day_3_14": "截点前72～360小时",
        },
        "encounter_count": 1,
        "event_count": len(retained),
        "window_event_counts": {
            bucket: windows[bucket]["event_count"] for bucket in BUCKET_ORDER
        },
        "basic_profile": {"age": age, "gender": gender, "department": department},
        "clinical_input": clinical_input,
        "clinical_text": clinical_input,
        "observation_windows": windows,
        "objective_trends": trends,
        "quality_flags": _summarize_quality(flags),
        "provenance": provenance,
        "compression": {"status": compression_status},
        "included_sections": ["基本资料"]
        + [BUCKET_LABELS[bucket] for bucket in BUCKET_ORDER if windows[bucket]["event_count"]]
        + (["总体变化"] if trends else []),
        "excluded_fields": list(EXCLUDED_FIELDS),
        "privacy_notice": "仅使用当前就诊预测截点前的脱敏记录；不返回原始标识、绝对日期或回顾性标签。",
        "sources": [get_source_name(), "确定性时间窗与字段对齐规则"],
    }


@lru_cache(maxsize=256)
def _cached_context(
    encounter_key: str,
    signature: tuple[str, int, int],
    context_model: str,
    compression_enabled: bool,
) -> dict[str, Any]:
    del signature, context_model
    return _build_context(
        encounter_key,
        use_llm_compression=compression_enabled,
    )


def get_patient_clinical_context(
    encounter_key: str,
    as_of_time: str | datetime | None = None,
    *,
    use_llm_compression: bool = True,
    compressor_client: Any | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Return one encounter's pre-cutoff facts grouped into past observation windows."""
    enabled = use_llm_compression and context_compression_enabled()
    if progress_callback is not None:
        try:
            progress_callback(
                {
                    "type": "context_progress",
                    "label": (
                        "正在按观察时间窗整理资料并压缩重复记录"
                        if enabled
                        else "正在按观察时间窗整理资料"
                    ),
                }
            )
        except Exception:
            pass
    if as_of_time is not None or compressor_client is not None or not use_llm_compression:
        result = _build_context(
            encounter_key,
            as_of_time,
            use_llm_compression=use_llm_compression,
            compressor_client=compressor_client,
        )
    else:
        result = deepcopy(
            _cached_context(
                encounter_key,
                workbook_signature(),
                get_context_model(),
                enabled,
            )
        )
    if progress_callback is not None:
        try:
            progress_callback(
                {"type": "context_progress", "label": "已完成症状与检查变化整理"}
            )
        except Exception:
            pass
    return result
