from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from services.database import (
    connect_readonly,
    database_signature,
    quote_identifier,
    resolve_display_id,
)


DIAGNOSIS_COLUMNS = (
    "入院诊断",
    "首页门急诊诊断",
    "急诊-主诊断名称",
    "诊断名称",
    "门诊-诊断",
    "初步诊断",
)

TIMELINE_FIELDS = (
    ("门诊-记录时间", "门诊记录", "门诊记录", ("门诊-诊断", "门诊-辅助检查"), "门诊"),
    ("急诊-就诊时间", "急诊记录", "急诊就诊记录", ("急诊-主诊断名称",), "急诊"),
    ("就诊日期时间", "就诊记录", "就诊记录", DIAGNOSIS_COLUMNS, "就诊信息"),
    ("首页入院时间", "入院记录", "入院记录", ("入院诊断", "首页门急诊诊断"), "入院信息"),
    ("检查日期", "检查记录", "检查记录", ("检查名称",), "检查信息"),
    ("超声-检查时间", "心脏超声", "超声检查记录", ("超声-检查名称", "超声-超声提示"), "心脏超声"),
    ("介入-手术日期", "介入治疗", "介入手术记录", ("介入-手术名称", "介入-本次手术"), "介入记录"),
    ("手术开始时间", "手术治疗", "手术记录", ("手术名称",), "手术记录"),
    ("诊断时间", "诊断记录", "诊断记录", ("诊断名称",), "诊断信息"),
    ("采集时间", "检验记录", "检验采集记录", ("检验套名称",), "检验信息"),
    ("采集时间_2", "检验记录", "检验采集记录（第2组）", ("检验套名称_2",), "检验信息"),
    ("采集时间_3", "检验记录", "检验采集记录（第3组）", ("检验套名称_3",), "检验信息"),
    ("日常病程记录时间", "病程记录", "日常病程记录", (), "病程记录"),
    ("上级查房时间", "查房记录", "上级查房记录", (), "查房记录"),
    ("记录时间", "病历记录", "病历记录", ("初步诊断",), "病历信息"),
)

NARRATIVE_FEATURE_FIELDS = (
    ("病情信息", "主诉", "主诉", "入院记录"),
    ("病情信息", "现病史", "现病史", "入院记录"),
    ("病情信息", "既往史", "既往史", "入院记录"),
    ("病情信息", "个人史", "个人史", "入院记录"),
    ("病情信息", "吸烟史", "吸烟史", "入院记录"),
    ("病情信息", "饮酒史", "饮酒史", "入院记录"),
    ("病情信息", "家族史", "家族史", "入院记录"),
    ("查体与检查", "体格检查", "体格检查(生命体征、一般情况)", "入院记录"),
    ("查体与检查", "专科检查", "专科检查", "入院记录"),
    ("查体与检查", "辅助检查", "辅助检查", "入院记录"),
)

NARRATIVE_FALLBACK_COLUMNS = {
    "主诉": ("门诊-主诉",),
    "现病史": ("门诊-现病史",),
    "既往史": ("门诊-既往史和其他病史",),
    "体格检查(生命体征、一般情况)": ("门诊-体格检查",),
}

ULTRASOUND_FEATURE_FIELDS = (
    ("超声检查名称", "超声-检查名称"),
    ("主动脉窦部", "超声-主动脉窦部"),
    ("升主动脉内径", "超声-升主动脉内径"),
    ("左房", "超声-左房"),
    ("右房", "超声-右房"),
    ("厚度", "超声-厚度"),
    ("运动幅度", "超声-运动幅度"),
    ("舒末内径", "超声-舒末内径"),
    ("收末内径", "超声-收末内径"),
    ("后壁厚度", "超声-后壁厚度"),
    ("后壁运动幅度", "超声-后壁运动幅度"),
    ("前后径", "超声-前后径"),
    ("左右径", "超声-左右径"),
    ("流出道", "超声-流出道"),
    ("缩短分数", "超声-缩短分数"),
    ("E波最大流速", "超声-E波最大流速"),
    ("A波最大流速", "超声-A波最大流速"),
    ("主动脉最大流速", "超声-主动脉最大流速"),
    ("肺动脉最大流速", "超声-肺动脉最大流速"),
    ("超声提示", "超声-超声提示"),
)

SOURCE_FIELD_EXCLUSIONS = frozenset(
    {"regno", "admno", "_patient_id", "label", "cutoff_time"}
)
BASIC_SOURCE_FIELDS = frozenset(
    {"年龄", "性别", "就诊类型", "生活能力评分-入院", "身高", "体重"}
)
HISTORY_SOURCE_FIELDS = frozenset(
    {
        "主诉", "现病史", "既往史", "个人史", "饮酒史", "家族史", "吸烟史",
        "体格检查(生命体征、一般情况)", "专科检查", "辅助检查",
    }
)
EXAM_SOURCE_FIELDS = frozenset(
    {"检查日期", "报告日期", "检查名称", "检查类型", "检查所见", "检查结果"}
)
NURSING_SOURCE_FIELDS = frozenset(
    {"测量时间", "项目名称", "测量值", "护理时间"}
)
MEDICATION_SOURCE_FIELDS = frozenset(
    {
        "用药开始时间", "用药结束时间", "单次剂量", "药物剂量单位", "用药剂型",
        "药物医嘱周期", "用药频次", "药品通用名称", "用药方式",
    }
)
LAB_SOURCE_FIELDS = frozenset(
    {"采集时间", "检验套名称", "检验项名称", "检验项值", "单位", "异常提示", "正常值范围"}
)
COURSE_SOURCE_FIELDS = frozenset(
    {
        "日常病程记录时间", "日常病程", "上级查房时间", "上级查房记录",
        "记录时间", "病例特点", "诊疗计划",
    }
)
DIAGNOSIS_SOURCE_FIELDS = frozenset(
    set(DIAGNOSIS_COLUMNS)
    | {
        "术前诊断", "介入-术前诊断", "诊断时间", "诊断备注", "是否主要诊断",
        "诊断状态", "ICD9编码", "诊断科室", "诊断依据",
    }
)


def _clean_text(value: object, limit: int = 160) -> str:
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    text = re.sub(r"(?<!\d)\d{6,}(?!\d)", "[已脱敏]", text)
    text = re.sub(
        r"(姓名|身份证号?|住院号|病案号|登记号|手机号|电话|患者/家属签字|家属签字)\s*[:：]?\s*[^;，,。 ]+",
        r"\1：[已脱敏]",
        text,
    )
    return text[:limit]


def _parts(value: object, limit: int = 120) -> list[str]:
    text = _clean_text(value, limit=20_000)
    if not text:
        return []
    return list(
        dict.fromkeys(
            part
            for item in text.split(";")
            if (part := _clean_text(item, limit=limit))
        )
    )


def _first(row: pd.Series, columns: Iterable[str], limit: int = 120) -> str:
    for column in columns:
        if column not in row.index:
            continue
        values = _parts(row.get(column), limit=limit)
        if values:
            return values[0]
    return ""


def _number(value: object) -> float | None:
    text = _clean_text(value, limit=80).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    parsed = float(match.group())
    return parsed if 0 <= parsed <= 200 else None


def _format_number(value: float) -> str:
    return f"{value:.0f}" if value.is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")


def _lvef_result(value: object) -> str:
    values = [
        parsed
        for part in _parts(value, limit=40)
        if (parsed := _number(part)) is not None and parsed <= 100
    ]
    if not values:
        return ""
    if len(values) == 1:
        return f"{_format_number(values[0])}%"
    minimum, maximum = min(values), max(values)
    if minimum == maximum:
        return f"{_format_number(minimum)}%（多次记录）"
    return f"{_format_number(minimum)}–{_format_number(maximum)}%（多次记录）"


def _parse_datetime(value: object) -> datetime | None:
    text = _clean_text(value, limit=60)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _datetimes(value: object) -> list[datetime]:
    values: list[datetime] = []
    seen: set[datetime] = set()
    for part in _parts(value, limit=60):
        parsed = _parse_datetime(part)
        if parsed is not None and parsed not in seen:
            seen.add(parsed)
            values.append(parsed)
    return values


def _relative_time(event_time: datetime | None, cutoff_time: datetime | None) -> str:
    if event_time is None or cutoff_time is None:
        return "15天窗口内"
    days = (cutoff_time.date() - event_time.date()).days
    if days == 0:
        return "窗口截止日"
    return f"截止前 {days} 天"


def _row_summary(row: pd.Series) -> dict[str, Any]:
    age = _number(row.get("年龄"))
    label = _number(row.get("label"))
    diagnosis_value = _first(row, DIAGNOSIS_COLUMNS, limit=110)
    ward = _first(row, ("诊断科室",), limit=50) or "—"
    return {
        "patient_id": str(row["_patient_id"]),
        "age": int(age) if age is not None else "—",
        "gender": _clean_text(row.get("性别"), limit=10) or "—",
        "diagnosis": diagnosis_value or "暂无诊断记录",
        "has_structured_diagnosis": bool(diagnosis_value),
        "ward": ward,
        "cohort_group": (
            "心脏破裂组" if label == 1 else "非破裂组" if label == 0 else "—"
        ),
    }


def get_patients(limit: int = 600) -> list[dict[str, Any]]:
    # Keep patient choices and display IDs identical to the auxiliary-diagnosis page.
    from services.data_api import get_patients as get_database_patients

    return get_database_patients(limit=max(1, int(limit)), cohort_label=1)


@st.cache_data(max_entries=512, show_spinner=False)
def _load_database_patient_row(
    patient_id: str,
    signature: tuple[int, int, int, int],
) -> pd.Series:
    """Load exactly one source row for a de-identified patient ID."""
    del signature
    normalized_id = str(patient_id or "").strip().upper()
    location = resolve_display_id(normalized_id)
    if not location:
        raise KeyError(f"未找到患者编号 {normalized_id}")

    table, rowid = location
    with connect_readonly() as connection:
        source_row = connection.execute(
            f"SELECT * FROM {quote_identifier(table)} WHERE rowid = ?",
            (rowid,),
        ).fetchone()
    if source_row is None:
        raise KeyError(f"患者记录已不存在：{normalized_id}")

    row = dict(source_row)
    row["_patient_id"] = normalized_id
    if not _clean_text(row.get("label"), limit=10):
        row["label"] = 1 if table == "break_data" else 0
    return pd.Series(row)


def _get_patient_row(patient_id: str) -> pd.Series:
    normalized_id = str(patient_id or "").strip().upper()
    return _load_database_patient_row(normalized_id, database_signature()).copy()


def get_patient_summary(patient_id: str) -> dict[str, Any]:
    return _row_summary(_get_patient_row(patient_id))


def _timeline_summary(row: pd.Series, columns: Iterable[str]) -> str:
    values: list[str] = []
    for column in columns:
        if column in row.index:
            values.extend(_parts(row.get(column), limit=90))
    values = list(dict.fromkeys(values))[:3]
    return "；".join(values) if values else "已记录"


def _build_timeline(row: pd.Series, patient_id: str) -> list[dict[str, Any]]:
    cutoff = _parse_datetime(row.get("cutoff_time"))
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    seen: set[tuple[object, str]] = set()
    for time_column, event_type, title, summary_columns, source in TIMELINE_FIELDS:
        if time_column not in row.index:
            continue
        summary = _timeline_summary(row, summary_columns)
        for event_time in _datetimes(row.get(time_column)):
            if cutoff is not None:
                days = (cutoff.date() - event_time.date()).days
                if not 0 <= days <= 15:
                    continue
            key = (event_time.date(), title)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                (
                    event_time,
                    {
                        "time": _relative_time(event_time, cutoff),
                        "type": event_type,
                        "title": title,
                        "summary": summary,
                        "source": source,
                    },
                )
            )
    candidates.sort(key=lambda item: item[0])
    return [
        event | {"id": f"{patient_id}-E{index:02d}", "raw": event["summary"]}
        for index, (_, event) in enumerate(candidates, 1)
    ]


def _evidence_time(row: pd.Series, column: str) -> str:
    cutoff = _parse_datetime(row.get("cutoff_time"))
    times = _datetimes(row.get(column)) if column in row.index else []
    return _relative_time(max(times), cutoff) if times else "15天窗口内"


def _build_records(row: pd.Series, summary: dict[str, Any], lvef: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    if summary["has_structured_diagnosis"]:
        records.append(
            {
                "title": "主要诊断记录",
                "detail": summary["diagnosis"],
                "time": _evidence_time(row, "诊断时间"),
                "source": "诊断信息",
            }
        )

    echo_notes = _parts(row.get("超声-超声提示"), limit=130)
    if lvef or echo_notes:
        details = ([f"LVEF {lvef}"] if lvef else []) + echo_notes[:2]
        records.append(
            {
                "title": "心脏超声记录",
                "detail": "；".join(details),
                "time": _evidence_time(row, "超声-检查时间"),
                "source": "心脏超声",
            }
        )

    checks = _parts(row.get("检查名称"), limit=80)
    if checks:
        records.append(
            {
                "title": "检查项目记录",
                "detail": "；".join(checks[:4]),
                "time": _evidence_time(row, "检查日期"),
                "source": "检查信息",
            }
        )

    procedures = list(
        dict.fromkeys(
            _parts(row.get("介入-手术名称"), limit=90)
            + _parts(row.get("手术名称"), limit=90)
        )
    )
    if procedures:
        time_column = "介入-手术日期" if _datetimes(row.get("介入-手术日期")) else "手术开始时间"
        records.append(
            {
                "title": "手术与介入记录",
                "detail": "；".join(procedures[:4]),
                "time": _evidence_time(row, time_column),
                "source": "手术记录",
            }
        )
    return records


def _joined_value(
    row: pd.Series,
    column: str,
    *,
    item_limit: int = 180,
    max_items: int = 8,
) -> str:
    if column not in row.index:
        return ""
    values = _parts(row.get(column), limit=item_limit)[:max_items]
    return "；".join(values)


def _relative_values(row: pd.Series, column: str, cutoff: datetime | None) -> str:
    if column not in row.index:
        return ""
    values = [
        _relative_time(value, cutoff)
        for value in _datetimes(row.get(column))
    ]
    return "；".join(dict.fromkeys(values))


def _append_feature(
    features: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    category: str,
    name: str,
    result: str,
    source: str,
) -> None:
    result = _clean_text(result, limit=520)
    if not result or result == "—":
        return
    key = (category, name, result)
    if key in seen:
        return
    seen.add(key)
    features.append(
        {"category": category, "name": name, "result": result, "source": source}
    )


def _append_laboratory_features(
    row: pd.Series,
    features: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
) -> None:
    groups = (
        ("第1组", "检验项名称", "检验项值", "单位", "异常提示", "正常值范围"),
        ("第2组", "检验项名称_2", "检验项值_2", "单位_2", "异常提示_2", "正常值范围_2"),
        ("第3组", "检验项名称_3", "检验项值_3", "单位_3", "异常提示_3", "正常值范围_3"),
    )
    for group_label, name_column, value_column, unit_column, flag_column, range_column in groups:
        names = _parts(row.get(name_column), limit=100)
        values = _parts(row.get(value_column), limit=80)
        units = _parts(row.get(unit_column), limit=30)
        flags = _parts(row.get(flag_column), limit=30)
        ranges = _parts(row.get(range_column), limit=80)
        if not names:
            continue
        if (
            len(names) == 1
            and len(values) == 1
            and len(units) <= 1
            and len(flags) <= 1
            and len(ranges) <= 1
        ):
            result = values[0] + (f" {units[0]}" if units else "")
            details = []
            if flags:
                details.append(f"标记：{flags[0]}")
            if ranges:
                details.append(f"参考范围：{ranges[0]}")
            if details:
                result += f"（{'；'.join(details)}）"
            _append_feature(
                features,
                seen,
                "检验",
                names[0],
                result,
                f"检验结果（{group_label}）",
            )
        else:
            _append_feature(
                features,
                seen,
                "检验",
                f"检验项目记录（{group_label}）",
                "；".join(names[:10]),
                f"检验结果（{group_label}）",
            )


def _base_source_column(column: str) -> str:
    return re.sub(r"_([23])$", "", str(column))


def _display_source_column(column: str) -> str:
    match = re.search(r"_([23])$", str(column))
    base = _base_source_column(column)
    return f"{base}（第{match.group(1)}组）" if match else base


def _source_field_category(column: str) -> tuple[str, str]:
    base = _base_source_column(column)
    if base in BASIC_SOURCE_FIELDS:
        return "基础信息", "基础与就诊信息"
    if base.startswith("门诊-"):
        return "门诊记录", "门诊记录"
    if base.startswith("急诊-"):
        return "急诊记录", "急诊记录"
    if base.startswith("超声-"):
        return "心脏超声", "心脏超声"
    if base.startswith("介入-"):
        return "治疗记录", "介入记录"
    if base in LAB_SOURCE_FIELDS:
        return "检验", "检验结果原字段"
    if base in MEDICATION_SOURCE_FIELDS or base.startswith("医嘱"):
        return "用药与医嘱", "用药与医嘱记录"
    if base in NURSING_SOURCE_FIELDS:
        return "生命体征", "护理测量原字段"
    if base in EXAM_SOURCE_FIELDS:
        return "查体与检查", "检查信息"
    if base in HISTORY_SOURCE_FIELDS:
        return "病情信息", "入院记录"
    if base in DIAGNOSIS_SOURCE_FIELDS:
        return "诊断", "诊断信息"
    if base in COURSE_SOURCE_FIELDS:
        return "病程记录", "病程与查房记录"
    if base in {"手术结束时间", "手术开始时间", "手术名称", "拟行手术名称"}:
        return "治疗记录", "手术记录"
    return "其他记录", "原始数据表"


def _source_field_result(
    row: pd.Series,
    column: str,
    cutoff: datetime | None,
) -> str:
    raw = _clean_text(row.get(column), limit=20_000)
    if not raw:
        return ""
    base = _base_source_column(column)
    if "时间" in base or "日期" in base:
        times = _datetimes(row.get(column))
        if times:
            return "；".join(
                dict.fromkeys(_relative_time(value, cutoff) for value in times)
            )
    return raw.replace(";", "；")


def _build_no_diagnosis_features(
    row: pd.Series,
    summary: dict[str, Any],
    cutoff: datetime | None,
) -> list[dict[str, str]]:
    """Expose every non-empty safe source field without cross-column pairing."""
    features: list[dict[str, str]] = [
        {
            "category": "基础信息",
            "name": "病例分组",
            "result": summary["cohort_group"],
            "source": "数据表标签",
            "source_column": "label",
        }
    ]
    for raw_column in row.index:
        column = str(raw_column)
        if column in SOURCE_FIELD_EXCLUSIONS:
            continue
        result = _source_field_result(row, column, cutoff)
        if not result:
            continue
        category, source = _source_field_category(column)
        features.append(
            {
                "category": category,
                "name": _display_source_column(column),
                "result": result,
                "source": source,
                "source_column": column,
            }
        )
    return features


def _build_features(
    row: pd.Series,
    summary: dict[str, Any],
    cutoff: datetime | None,
    admission_time: str,
    lvef: str,
) -> list[dict[str, str]]:
    if not summary["has_structured_diagnosis"]:
        return _build_no_diagnosis_features(row, summary, cutoff)

    features: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    _append_feature(
        features,
        seen,
        "基础信息",
        "年龄",
        f"{summary['age']}岁" if summary["age"] != "—" else "",
        "基础信息",
    )
    _append_feature(features, seen, "基础信息", "性别", summary["gender"], "基础信息")
    _append_feature(
        features,
        seen,
        "基础信息",
        "病例分组",
        summary["cohort_group"],
        "数据表标签",
    )
    for label, column in (
        ("就诊类型", "就诊类型"),
        ("急诊就诊类别", "急诊-就诊类别"),
        ("身高记录", "身高"),
        ("体重记录", "体重"),
        ("入院生活能力评分", "生活能力评分-入院"),
    ):
        _append_feature(
            features,
            seen,
            "基础信息",
            label,
            _joined_value(row, column),
            "就诊信息",
        )

    diagnosis_seen: set[str] = set()
    for label, value, source in (
        (
            "主要诊断",
            summary["diagnosis"] if summary["has_structured_diagnosis"] else "",
            "诊断信息",
        ),
        ("首页门急诊诊断", _joined_value(row, "首页门急诊诊断"), "首页信息"),
        ("急诊主诊断", _joined_value(row, "急诊-主诊断名称"), "急诊记录"),
        ("初步诊断", _joined_value(row, "初步诊断"), "病历记录"),
        ("术前诊断", _joined_value(row, "术前诊断"), "手术记录"),
        ("介入术前诊断", _joined_value(row, "介入-术前诊断"), "介入记录"),
    ):
        if value and value not in diagnosis_seen:
            diagnosis_seen.add(value)
            _append_feature(features, seen, "诊断", label, value, source)
    _append_feature(features, seen, "诊断", "诊断科室", summary["ward"], "诊断信息")

    for label, value, source in (
        ("入院记录", admission_time, "入院信息"),
        ("急诊就诊记录", _relative_values(row, "急诊-就诊时间", cutoff), "急诊记录"),
        ("出院记录", _relative_values(row, "出院日期时间", cutoff), "出院信息"),
    ):
        _append_feature(features, seen, "就诊时间", label, value, source)

    for category, label, column, source in NARRATIVE_FEATURE_FIELDS:
        value = _joined_value(row, column, item_limit=320, max_items=3)
        if not value:
            for fallback_column in NARRATIVE_FALLBACK_COLUMNS.get(column, ()):
                value = _joined_value(
                    row,
                    fallback_column,
                    item_limit=320,
                    max_items=3,
                )
                if value:
                    break
        _append_feature(features, seen, category, label, value, source)

    vital_names = _parts(row.get("项目名称"), limit=90)
    vital_values = _parts(row.get("测量值"), limit=70)
    if len(vital_names) == 1 and len(vital_values) == 1:
        _append_feature(
            features,
            seen,
            "生命体征",
            vital_names[0],
            vital_values[0],
            "护理测量",
        )
    elif vital_names:
        _append_feature(
            features,
            seen,
            "生命体征",
            "生命体征项目记录",
            "；".join(vital_names[:10]),
            "护理测量",
        )

    for label, column in (
        ("检查项目", "检查名称"),
        ("检查类型", "检查类型"),
        ("检查结果记录", "检查结果"),
    ):
        _append_feature(
            features,
            seen,
            "查体与检查",
            label,
            _joined_value(row, column, item_limit=220, max_items=6),
            "检查信息",
        )

    _append_feature(features, seen, "心脏超声", "LVEF", lvef, "心脏超声")
    for label, column in ULTRASOUND_FEATURE_FIELDS:
        _append_feature(
            features,
            seen,
            "心脏超声",
            label,
            _joined_value(row, column, item_limit=220, max_items=8),
            "心脏超声",
        )

    for category, label, column, source in (
        ("治疗记录", "介入手术名称", "介入-手术名称", "介入记录"),
        ("治疗记录", "介入结论", "介入-结论", "介入记录"),
        ("治疗记录", "手术名称", "手术名称", "手术记录"),
        ("治疗记录", "拟行手术", "拟行手术名称", "手术记录"),
        ("用药与医嘱", "用药记录", "药品通用名称", "用药医嘱"),
        ("用药与医嘱", "用药方式记录", "用药方式", "用药医嘱"),
        ("用药与医嘱", "医嘱名称", "医嘱名称新", "医嘱记录"),
        ("用药与医嘱", "医嘱类型", "医嘱类型", "医嘱记录"),
    ):
        _append_feature(
            features,
            seen,
            category,
            label,
            _joined_value(row, column, item_limit=180, max_items=10),
            source,
        )

    _append_laboratory_features(row, features, seen)
    return features


def get_patient_detail(patient_id: str) -> dict[str, Any]:
    row = _get_patient_row(patient_id)
    summary = _row_summary(row)
    cutoff = _parse_datetime(row.get("cutoff_time"))
    admission_times = _datetimes(row.get("首页入院时间")) or _datetimes(row.get("就诊日期时间"))
    admission_time = _relative_time(admission_times[0], cutoff) if admission_times else "—"
    lvef = _lvef_result(row.get("超声-射血分数"))
    timeline = _build_timeline(row, patient_id)

    features = _build_features(row, summary, cutoff, admission_time, lvef)

    records = _build_records(row, summary, lvef)
    handover_parts = [f"{patient_id}，{summary['age']}岁，{summary['gender']}"]
    if summary["has_structured_diagnosis"]:
        handover_parts.append(f"主要诊断：{summary['diagnosis']}")
    if summary["ward"] != "—":
        handover_parts.append(f"诊断科室：{summary['ward']}")
    if lvef:
        handover_parts.append(f"LVEF记录：{lvef}")
    if timeline:
        handover_parts.append(f"15天窗口内共有{len(timeline)}个时间轴节点")

    return {
        "profile": summary
        | {
            "admission_time": admission_time,
        },
        "important_features": features,
        "timeline": timeline,
        "records": records,
        "handover": "。".join(handover_parts) + "。",
    }
