from __future__ import annotations

import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK_PATH = PROJECT_ROOT / "clean_非破裂完整版（15天窗口）.xlsx"
SOURCE_NAME = DEFAULT_WORKBOOK_PATH.name

DIAGNOSIS_COLUMNS = (
    "入院诊断",
    "首页门急诊诊断",
    "急诊-主诊断名称",
    "诊断名称",
    "门诊-诊断",
    "初步诊断",
)

ADMISSION_COLUMNS = (
    "首页入院时间",
    "就诊日期时间",
    "急诊-就诊时间",
    "门诊-记录时间",
)

SURGERY_NAME_COLUMNS = (
    "介入-手术名称",
    "介入-本次手术",
    "手术名称",
    "拟行手术名称",
)

SEARCH_TEXT_COLUMNS = (
    *DIAGNOSIS_COLUMNS,
    "诊断科室",
    *SURGERY_NAME_COLUMNS,
)

FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "诊断信息": (
        "入院诊断",
        "首页门急诊诊断",
        "门诊-诊断",
        "急诊-主诊断名称",
        "急诊-其他诊断名称1",
        "急诊-其他诊断名称2",
        "急诊-其他诊断名称3",
        "初步诊断",
        "诊断名称",
        "诊断时间",
        "诊断科室",
        "诊断备注",
        "诊断依据",
        "是否主要诊断",
        "诊断状态",
        "ICD9编码",
        "术前诊断",
        "介入-术前诊断",
    ),
    "检查与检验": (
        "检查日期",
        "报告日期",
        "检查名称",
        "检查类型",
        "检查所见",
        "检查结果",
        "体格检查(生命体征、一般情况)",
        "专科检查",
        "辅助检查",
        "超声-检查时间",
        "超声-检查名称",
        "超声-射血分数",
        "超声-超声描述",
        "超声-超声提示",
        "采集时间",
        "检验套名称",
        "检验项名称",
        "检验项值",
        "单位",
        "异常提示",
        "正常值范围",
        "采集时间_2",
        "检验套名称_2",
        "检验项名称_2",
        "检验项值_2",
        "单位_2",
        "异常提示_2",
        "正常值范围_2",
        "采集时间_3",
        "检验套名称_3",
        "检验项名称_3",
        "检验项值_3",
        "单位_3",
        "异常提示_3",
        "正常值范围_3",
    ),
    "用药与医嘱": (
        "用药开始时间",
        "用药结束时间",
        "药品通用名称",
        "单次剂量",
        "药物剂量单位",
        "用药剂型",
        "药物医嘱周期",
        "用药频次",
        "用药方式",
        "医嘱开始时间",
        "医嘱结束时间",
        "医嘱名称新",
        "医嘱类型",
        "医嘱备注",
    ),
    "手术信息": (
        "介入-手术日期",
        "介入-术前诊断",
        "介入-本次手术",
        "介入-手术名称",
        "介入-结论",
        "手术开始时间",
        "手术结束时间",
        "手术名称",
        "拟行手术名称",
        "术前诊断",
    ),
    "病程记录": (
        "主诉",
        "现病史",
        "既往史",
        "个人史",
        "饮酒史",
        "家族史",
        "吸烟史",
        "门诊-主诉",
        "门诊-现病史",
        "门诊-既往史和其他病史",
        "门诊-病情变化及处置",
        "日常病程记录时间",
        "日常病程",
        "上级查房时间",
        "上级查房记录",
        "记录时间",
        "病例特点",
        "诊疗计划",
    ),
}


def _streamlit_setting(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


def get_workbook_path() -> Path:
    configured = os.getenv("PATIENT_WORKBOOK_XLSX", "").strip() or _streamlit_setting(
        "PATIENT_WORKBOOK_XLSX"
    )
    path = Path(configured).expanduser() if configured else DEFAULT_WORKBOOK_PATH
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到患者工作簿：{path}")
    return path


def workbook_signature() -> tuple[str, int, int]:
    path = get_workbook_path()
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def get_source_name() -> str:
    """Return the active workbook name without exposing its absolute path."""
    return get_workbook_path().name


def _normalize_column(column: object) -> str:
    text = str(column)
    if text.endswith(".1"):
        return text[:-2] + "_2"
    if text.endswith(".2"):
        return text[:-2] + "_3"
    return text


def _id_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def _clean_text(value: object, limit: int = 5000) -> str:
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"", "nan", "none", "null", "nat"}:
        return ""
    text = re.sub(
        r"(姓名|身份证号?|手机号|电话|患者/家属签字|家属签字)\s*[:：]?\s*[^;，,。 ]+",
        r"\1：[已脱敏]",
        text,
    )
    return text[:limit]


def _parts(value: object, limit: int = 5000) -> list[str]:
    text = _clean_text(value, limit=100_000)
    if not text:
        return []
    values = [_clean_text(item, limit=limit) for item in text.split(";")]
    return list(dict.fromkeys(item for item in values if item))


def _first(row: pd.Series, columns: Iterable[str], limit: int = 180) -> str:
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
    number = float(match.group())
    return number if math.isfinite(number) else None


def _parse_datetime(value: object) -> datetime | None:
    text = _clean_text(value, limit=80)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _datetimes(value: object) -> list[datetime]:
    parsed = [_parse_datetime(item) for item in _parts(value, limit=80)]
    return sorted(dict.fromkeys(item for item in parsed if item is not None))


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "暂无记录"
    if value.hour == 0 and value.minute == 0 and value.second == 0:
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M")


def _first_datetime(row: pd.Series, columns: Iterable[str]) -> datetime | None:
    values: list[datetime] = []
    for column in columns:
        if column in row.index:
            values.extend(_datetimes(row.get(column)))
    return min(values) if values else None


def _last_datetime(row: pd.Series, columns: Iterable[str]) -> datetime | None:
    values: list[datetime] = []
    for column in columns:
        if column in row.index:
            values.extend(_datetimes(row.get(column)))
    return max(values) if values else None


def make_encounter_key(regno: object, admno: object) -> str:
    return f"{_id_text(regno)}::{_id_text(admno)}"


@st.cache_data(max_entries=4, show_spinner=False)
def _load_workbook(path_text: str, modified_ns: int, size: int) -> pd.DataFrame:
    del modified_ns, size
    source = pd.read_excel(path_text, sheet_name=0, dtype=str)
    source.columns = [_normalize_column(column) for column in source.columns]
    if "label" not in source.columns:
        raise ValueError("工作簿缺少 label 字段，无法识别有效数据行。")
    source["label"] = source["label"].map(lambda value: _clean_text(value, 20))
    source = source.loc[source["label"].isin({"0", "1"})].copy()
    for column in ("regno", "admno"):
        if column not in source.columns:
            raise ValueError(f"工作簿缺少必要标识字段：{column}")
        source[column] = source[column].map(_id_text)
    source = source.loc[(source["regno"] != "") & (source["admno"] != "")].copy()
    source["_encounter_key"] = [
        make_encounter_key(regno, admno)
        for regno, admno in zip(source["regno"], source["admno"])
    ]
    source = source.drop_duplicates("_encounter_key", keep="first").reset_index(drop=True)
    return source


def get_source_dataframe() -> pd.DataFrame:
    path_text, modified_ns, size = workbook_signature()
    return _load_workbook(path_text, modified_ns, size).copy()


@st.cache_resource(max_entries=4, show_spinner=False)
def _source_index(signature: tuple[str, int, int]) -> pd.DataFrame:
    """Keep one read-only indexed frame for repeated encounter lookups."""
    source = _load_workbook(*signature)
    return source.set_index("_encounter_key", drop=False)


def _row_summary(row: pd.Series) -> dict[str, Any]:
    age = _number(row.get("年龄"))
    admission = _first_datetime(row, ADMISSION_COLUMNS)
    discharge = _last_datetime(row, ("出院日期时间",))
    cutoff = _last_datetime(row, ("cutoff_time",))
    surgery = _first(row, SURGERY_NAME_COLUMNS, limit=160)
    label = int(_number(row.get("label")) or 0)
    return {
        "encounter_key": str(row["_encounter_key"]),
        "regno": _id_text(row.get("regno")),
        "admno": _id_text(row.get("admno")),
        "age": int(age) if age is not None else None,
        "gender": _clean_text(row.get("性别"), 12) or "暂无记录",
        "admission_datetime": admission,
        "admission_time": format_datetime(admission),
        "discharge_datetime": discharge,
        "discharge_time": format_datetime(discharge),
        "cutoff_datetime": cutoff,
        "cutoff_time": format_datetime(cutoff),
        "diagnosis": _first(row, DIAGNOSIS_COLUMNS, limit=600) or "暂无诊断记录",
        "department": _first(row, ("诊断科室",), limit=100) or "暂无记录",
        "surgery": surgery or "暂无记录",
        "has_surgery_record": bool(surgery or _first_datetime(row, ("介入-手术日期", "手术开始时间", "手术结束时间"))),
        "label": label,
        "outcome": "回顾性目标事件已记录（label=1）" if label == 1 else "回顾性目标事件未记录（label=0）",
        "risk_level": "UNKNOWN",
        "risk_label": "无法判断",
        "prediction_time": "暂无模型结果",
        "risk_source": "工作簿未提供可验证的模型风险分层或预测时间字段",
    }


@st.cache_data(max_entries=4, show_spinner=False)
def _encounter_summaries(signature: tuple[str, int, int]) -> list[dict[str, Any]]:
    source = _load_workbook(*signature)
    return [_row_summary(row) for _, row in source.iterrows()]


def get_encounters() -> list[dict[str, Any]]:
    return [dict(item) for item in _encounter_summaries(workbook_signature())]


def get_encounter_dataframe() -> pd.DataFrame:
    frame = pd.DataFrame(get_encounters())
    if frame.empty:
        return frame
    frame["search_text"] = (
        frame[["regno", "admno", "diagnosis", "department", "surgery"]]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .str.lower()
    )
    return frame


def get_metrics() -> dict[str, Any]:
    frame = get_encounter_dataframe()
    if frame.empty:
        return {
            "patient_count": 0,
            "encounter_count": 0,
            "multi_visit_patient_count": 0,
            "target_event_count": 0,
            "surgery_record_count": 0,
            "risk_available": False,
        }
    visits = frame.groupby("regno")["admno"].nunique()
    return {
        "patient_count": int(frame["regno"].nunique()),
        "encounter_count": int(len(frame)),
        "multi_visit_patient_count": int((visits > 1).sum()),
        "target_event_count": int((frame["label"] == 1).sum()),
        "surgery_record_count": int(frame["has_surgery_record"].sum()),
        "risk_available": False,
        "high_risk_count": None,
        "medium_risk_count": None,
        "low_risk_count": None,
        "source_file": get_source_name(),
    }


def get_encounter_summary(encounter_key: str) -> dict[str, Any]:
    normalized = str(encounter_key or "").strip()
    for item in get_encounters():
        if item["encounter_key"] == normalized:
            return item
    raise KeyError(f"未找到就诊记录：{normalized}")


def _get_row(encounter_key: str) -> pd.Series:
    source = _source_index(workbook_signature())
    normalized = str(encounter_key or "").strip()
    if normalized not in source.index:
        raise KeyError(f"未找到就诊记录：{encounter_key}")
    return source.loc[normalized].copy()


def get_encounter_source_record(encounter_key: str) -> dict[str, Any]:
    """Return one selected workbook row for server-side clinical processing."""
    row = _get_row(encounter_key)
    return {str(column): value for column, value in row.to_dict().items()}


def get_patient_encounters(regno: str) -> list[dict[str, Any]]:
    normalized = _id_text(regno)
    return [item for item in get_encounters() if item["regno"] == normalized]


def _event_summary(row: pd.Series, columns: Iterable[str], fallback: str) -> str:
    values: list[str] = []
    for column in columns:
        if column in row.index:
            values.extend(_parts(row.get(column), limit=260))
    values = list(dict.fromkeys(values))
    if not values:
        return fallback
    return "；".join(values[:3])


def _add_events(
    events: list[dict[str, Any]],
    row: pd.Series,
    time_column: str,
    event_type: str,
    title: str,
    content_columns: Iterable[str],
    source: str,
    fallback: str = "该字段有记录",
) -> None:
    if time_column not in row.index:
        return
    times = _datetimes(row.get(time_column))
    if not times:
        return
    values: list[str] = []
    for column in content_columns:
        if column in row.index:
            values.extend(_parts(row.get(column), limit=800))
    values = list(dict.fromkeys(values))
    pairing_reliable = len(times) == 1 and len(values) <= 1
    if pairing_reliable and values:
        summary = values[0]
        detail = values[0]
    elif values:
        summary = "；".join(values[:3])
        detail = (
            "该行以分号聚合了多个时间或内容，工作簿没有事件级关联键；"
            "下列内容仅表示本次就诊范围内有记录，不按列表位置与时间强行配对：\n\n"
            + "；".join(values)
        )
    else:
        summary = fallback
        detail = fallback
    for event_time in times[:20]:
        events.append(
            {
                "datetime": event_time,
                "time": format_datetime(event_time),
                "type": event_type,
                "title": title,
                "summary": summary,
                "detail": detail,
                "source": source,
                "source_field": time_column,
            }
        )


def _build_timeline(row: pd.Series) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    specs = (
        ("门诊-记录时间", "门诊就诊", "门诊记录", ("门诊-主诉", "门诊-诊断"), "门诊记录"),
        ("急诊-就诊时间", "急诊就诊", "急诊就诊", ("急诊-主诊断名称", "急诊-主要就诊原因"), "急诊记录"),
        ("就诊日期时间", "就诊记录", "就诊记录", DIAGNOSIS_COLUMNS, "就诊信息"),
        ("首页入院时间", "入院", "入院", ("入院诊断", "首页门急诊诊断"), "首页入院信息"),
        ("记录时间", "入院记录", "病历记录", ("病例特点", "初步诊断", "诊疗计划"), "病历记录"),
        ("诊断时间", "诊断", "诊断记录", ("诊断名称", "诊断备注"), "诊断信息"),
        ("检查日期", "检查", "检查", ("检查名称", "检查结果"), "检查信息"),
        ("报告日期", "检查", "检查报告", ("检查名称", "检查结果"), "检查信息"),
        ("超声-检查时间", "检查", "心脏超声", ("超声-检查名称", "超声-超声提示"), "心脏超声"),
        ("采集时间", "检验", "检验采集", ("检验套名称", "检验项名称"), "检验信息"),
        ("采集时间_2", "检验", "检验采集（第2组）", ("检验套名称_2", "检验项名称_2"), "检验信息"),
        ("采集时间_3", "检验", "检验采集（第3组）", ("检验套名称_3", "检验项名称_3"), "检验信息"),
        ("用药开始时间", "用药", "用药开始", ("药品通用名称", "用药方式"), "用药记录"),
        ("用药结束时间", "用药", "用药结束", ("药品通用名称",), "用药记录"),
        ("医嘱开始时间", "医嘱", "医嘱开始", ("医嘱名称新", "医嘱类型"), "医嘱记录"),
        ("医嘱结束时间", "医嘱", "医嘱结束", ("医嘱名称新",), "医嘱记录"),
        ("日常病程记录时间", "病程", "日常病程", ("日常病程",), "病程记录"),
        ("上级查房时间", "查房", "上级查房", ("上级查房记录",), "查房记录"),
        ("介入-手术日期", "手术", "介入或手术", ("介入-手术名称", "介入-本次手术", "介入-结论"), "介入记录"),
        ("手术开始时间", "手术", "手术开始", ("手术名称", "术前诊断"), "手术记录"),
        ("手术结束时间", "手术", "手术结束", ("手术名称",), "手术记录"),
        ("出院日期时间", "出院", "出院", (), "出院信息"),
        ("cutoff_time", "窗口截止", "15天数据窗口截止", (), "cutoff_time"),
    )
    for time_column, event_type, title, content_columns, source in specs:
        _add_events(
            events,
            row,
            time_column,
            event_type,
            title,
            content_columns,
            source,
        )
    events.sort(key=lambda event: event["datetime"])
    unique: list[dict[str, Any]] = []
    seen: set[tuple[datetime, str, str]] = set()
    for event in events:
        key = (event["datetime"], event["title"], event["source_field"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    encounter_key = str(row["_encounter_key"])
    return [
        {**event, "id": f"{encounter_key}-E{index:02d}"}
        for index, event in enumerate(unique, 1)
    ]


def _field_records(row: pd.Series, columns: Iterable[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for column in columns:
        if column not in row.index:
            continue
        value = _clean_text(row.get(column), limit=12_000)
        if not value:
            continue
        records.append(
            {
                "field": column,
                "value": value.replace(";", "；"),
                "source": f"工作簿字段：{column}",
                "is_long": len(value) > 220,
            }
        )
    return records


def get_encounter_detail(encounter_key: str) -> dict[str, Any]:
    row = _get_row(encounter_key)
    profile = _row_summary(row)
    visit_count = len(get_patient_encounters(profile["regno"]))
    profile["patient_visit_count"] = visit_count
    basic = [
        {"field": "患者编号", "value": profile["regno"], "source": "工作簿字段：regno", "is_long": False},
        {"field": "就诊编号", "value": profile["admno"], "source": "工作簿字段：admno", "is_long": False},
        {"field": "年龄", "value": f"{profile['age']} 岁" if profile["age"] is not None else "暂无记录", "source": "工作簿字段：年龄", "is_long": False},
        {"field": "性别", "value": profile["gender"], "source": "工作簿字段：性别", "is_long": False},
        {"field": "入院/就诊时间", "value": profile["admission_time"], "source": "首页入院时间 / 就诊日期时间 / 急诊-就诊时间 / 门诊-记录时间", "is_long": False},
        {"field": "出院时间", "value": profile["discharge_time"], "source": "工作簿字段：出院日期时间", "is_long": False},
        {"field": "15天窗口截止", "value": profile["cutoff_time"], "source": "工作簿字段：cutoff_time", "is_long": False},
        {"field": "同一患者就诊次数", "value": str(visit_count), "source": "按 regno 聚合、按 admno 区分", "is_long": False},
    ]
    risk = [
        {
            "field": "回顾性目标事件标签",
            "value": f"label={profile['label']}（{'已记录目标事件' if profile['label'] == 1 else '未记录目标事件'}）",
            "source": "工作簿字段：label",
            "is_long": False,
        },
        {
            "field": "风险等级",
            "value": "无法判断：工作簿未提供可验证的模型风险分层字段",
            "source": "字段可用性检查",
            "is_long": False,
        },
        {
            "field": "预测破裂时间",
            "value": "暂无模型结果；cutoff_time 仅作为数据窗口截止时间展示",
            "source": "字段语义核对",
            "is_long": False,
        },
    ]
    groups = {name: _field_records(row, fields) for name, fields in FIELD_GROUPS.items()}
    return {
        "profile": profile,
        "basic": basic,
        "groups": groups,
        "risk": risk,
        "timeline": _build_timeline(row),
        "source_file": get_source_name(),
    }


def get_patient_prediction_context(encounter_key: str) -> dict[str, Any]:
    detail = get_encounter_detail(encounter_key)
    profile = detail["profile"]
    sections: list[str] = [
        f"年龄：{profile['age'] if profile['age'] is not None else '未知'}",
        f"性别：{profile['gender']}",
        f"主要诊断：{profile['diagnosis']}",
    ]
    included_sections = ["基础信息", "诊断信息"]
    for group_name in ("检查与检验", "病程记录", "用药与医嘱", "手术信息"):
        records = detail["groups"].get(group_name, [])
        if not records:
            continue
        included_sections.append(group_name)
        sections.append(
            f"{group_name}："
            + "；".join(f"{item['field']}={item['value'][:500]}" for item in records[:12])
        )
    return {
        "patient_id": encounter_key,
        "clinical_text": "\n".join(sections),
        "included_sections": included_sections,
        "excluded_fields": ["regno", "admno", "label", "cutoff_time 后信息"],
        "sources": [get_source_name(), "工作簿字段清洗与就诊级聚合"],
    }


def get_data_status() -> dict[str, Any]:
    path = get_workbook_path()
    metrics = get_metrics()
    return {
        "path": str(path),
        "filename": path.name,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
        "mode": "Excel 只读",
        **metrics,
    }
