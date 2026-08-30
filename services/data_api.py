from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from statistics import median
from typing import Any, Iterable

from services.database import (
    SOURCE_TABLES,
    connect_readonly,
    database_signature,
    get_database_status,
    make_display_id,
    quote_identifier,
    resolve_display_id,
)


SOURCE_NAME = "院内结构化数据"
COHORT_LABELS = {1: "心脏破裂组", 0: "非破裂组"}
IMPORTANT_LABS = (
    ("心肌肌钙蛋白", ("肌钙蛋白", "TnI", "TnT")),
    ("D-二聚体", ("D-二聚体", "D-Dimer")),
    ("BNP / NT-proBNP", ("钠尿肽", "BNP", "NT-ProBNP")),
    ("肌酐", ("肌酐", "Cr)")),
    ("血红蛋白", ("血红蛋白",)),
    ("白细胞", ("白细胞",)),
    ("C反应蛋白", ("C-反应蛋白", "C反应蛋白", "CRP")),
    ("白蛋白", ("白蛋白",)),
)


def _clean_text(value: object, limit: int = 120) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    text = re.sub(r"(?<!\d)\d{6,}(?!\d)", "[已脱敏]", text)
    text = re.sub(r"(姓名|身份证号?|住院号|病案号|登记号|手机号|电话)\s*[:：]?\s*[^;，,。 ]+", r"\1：[已脱敏]", text)
    return text[:limit]


def _split(value: object) -> list[str]:
    text = _clean_text(value, limit=100_000)
    return [part.strip() for part in text.split(";")] if text else []


def _number(value: object) -> float | None:
    text = _clean_text(value, limit=80).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    parsed = float(match.group())
    return parsed if math.isfinite(parsed) and abs(parsed) < 1_000_000 else None


def _single_number(value: object) -> float | None:
    """Parse a numeric scalar only when the source field contains one item."""
    parts = [part for part in _split(value) if _clean_text(part, limit=80)]
    if len(parts) != 1:
        return None
    return _number(parts[0])


def _has_multiple_values(value: object) -> bool:
    return len([part for part in _split(value) if _clean_text(part, limit=80)]) > 1


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _first(row: dict[str, Any], columns: Iterable[str], limit: int = 100) -> str:
    for column in columns:
        value = _clean_text(row.get(column), limit=limit)
        if value:
            return value.split(";")[0].strip()
    return ""


def _parse_datetime(value: object) -> datetime | None:
    text = _clean_text(value, limit=60).split(";")[0]
    if not text:
        return None
    normalized = text.replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(normalized[: len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _relative_time(value: object, cutoff: object) -> str:
    event_time = _parse_datetime(value)
    cutoff_time = _parse_datetime(cutoff)
    if event_time and cutoff_time:
        delta = cutoff_time - event_time
        days = max(0, delta.days)
        return "窗口截止" if days == 0 else f"截止前 {days} 天"
    return "15天窗口内"


def _bundle_rows(
    row: dict[str, Any],
    name_column: str,
    value_column: str,
    unit_column: str | None = None,
    flag_column: str | None = None,
    time_column: str | None = None,
) -> list[dict[str, str]]:
    names = _split(row.get(name_column))
    values = _split(row.get(value_column))
    units = _split(row.get(unit_column)) if unit_column else []
    flags = _split(row.get(flag_column)) if flag_column else []
    times = _split(row.get(time_column)) if time_column else []
    if not names:
        return []

    # The uploaded patient-level files store many fields as independently
    # aggregated semicolon-separated sets.  Their positions are not event IDs:
    # a row may contain 14 names, 34 values, 14 times and no shared ordering.
    # Only a single unambiguous item can therefore be paired safely.
    can_pair = (
        len(names) == 1
        and len(values) == 1
        and len(units) <= 1
        and len(flags) <= 1
        and len(times) <= 1
    )
    if can_pair:
        return [
            {
                "item": _clean_text(names[0], 90),
                "value": _clean_text(values[0], 60) or "—",
                "unit": _clean_text(units[0], 30) if units else "",
                "flag": _clean_text(flags[0], 12) if flags else "",
                "time": _relative_time(times[0] if times else "", row.get("cutoff_time")),
                "pairing_status": "paired",
                "pairing_notice": "单项记录，名称与结果可直接配对。",
            }
        ]

    notice = (
        f"原数据库为分别汇总的集合（名称{len(names)}项、数值{len(values)}项、"
        f"单位{len(units)}项、标记{len(flags)}项、时间{len(times)}项），"
        "缺少事件级关联键，不能按位置配对。"
    )
    return [
        {
            "item": _clean_text(name, 90),
            "value": "—",
            "unit": "",
            "flag": "",
            "time": "15天窗口内",
            "pairing_status": "unavailable",
            "pairing_notice": notice,
        }
        for name in names
        if _clean_text(name, 90)
    ]


def _extract_labs(row: dict[str, Any]) -> list[dict[str, str]]:
    labs: list[dict[str, str]] = []
    groups = (
        ("检验项名称", "检验项值", "单位", "异常提示", "采集时间"),
        ("检验项名称_2", "检验项值_2", "单位_2", "异常提示_2", "采集时间_2"),
        ("检验项名称_3", "检验项值_3", "单位_3", "异常提示_3", "采集时间_3"),
    )
    for columns in groups:
        labs.extend(_bundle_rows(row, *columns))
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in labs:
        key = (item["item"], item["value"], item["time"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    important_terms = tuple(term.lower() for _, terms in IMPORTANT_LABS for term in terms)
    unique.sort(
        key=lambda item: (
            0 if any(term in item["item"].lower() for term in important_terms) else 1,
            0 if item["flag"] else 1,
            item["item"],
        )
    )
    return unique


def _extract_vitals(row: dict[str, Any]) -> list[dict[str, str]]:
    values = _bundle_rows(row, "项目名称", "测量值", time_column="测量时间")
    keep_terms = ("收缩压", "舒张压", "心率", "脉搏", "呼吸", "体温", "腋温", "血压", "体重", "身高")
    return [item for item in values if any(term in item["item"] for term in keep_terms)]


def _lab_match(item_name: str, terms: Iterable[str]) -> bool:
    lowered = item_name.lower()
    return any(term.lower() in lowered for term in terms)


def _key_lab_rows(labs: list[dict[str, str]], limit: int = 16) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used_labels: set[str] = set()
    for label, terms in IMPORTANT_LABS:
        match = next((item for item in labs if _lab_match(item["item"], terms)), None)
        if match:
            selected.append(match | {"display_name": label, "source": "检验结果"})
            used_labels.add(label)
    for item in labs:
        if len(selected) >= limit:
            break
        if item["flag"] and not any(existing["item"] == item["item"] for existing in selected):
            selected.append(item | {"display_name": item["item"], "source": "检验结果"})
    return selected[:limit]


def _diagnosis(row: dict[str, Any]) -> str:
    return _first(
        row,
        ("入院诊断", "首页门急诊诊断", "急诊-主诊断名称", "诊断名称", "门诊-诊断", "初步诊断"),
        limit=100,
    ) or "暂无诊断记录"


def _ward(row: dict[str, Any]) -> str:
    value = _first(row, ("诊断科室",), limit=50)
    if "急诊" in value:
        return "急诊科"
    if "冠心病" in value or "心内" in value:
        return "心内科"
    return value or "院内队列"


def _feature_signals(row: dict[str, Any], labs: list[dict[str, str]], vitals: list[dict[str, str]]) -> list[str]:
    signals: list[str] = []
    age = _number(row.get("年龄"))
    ef = _single_number(row.get("超声-射血分数"))
    if age is not None and age >= 75:
        signals.append(f"高龄（{_format_number(age)}岁）")
    if ef is not None and ef < 40:
        signals.append(f"LVEF降低（{_format_number(ef)}%）")
    for vital in vitals:
        if vital.get("pairing_status") != "paired":
            continue
        value = _number(vital["value"])
        if value is None:
            continue
        if "收缩压" in vital["item"] and value < 90:
            signals.append(f"收缩压偏低（{_format_number(value)} mmHg）")
        elif any(term in vital["item"] for term in ("心率", "脉搏")) and value > 100:
            signals.append(f"心率偏快（{_format_number(value)} bpm）")
    for lab in _key_lab_rows(labs, limit=10):
        if lab.get("pairing_status") != "paired":
            continue
        flag = lab.get("flag", "").upper()
        if flag and flag not in {"N", "正常"}:
            signals.append(f"{lab['display_name']}异常（{flag}）")
    return list(dict.fromkeys(signals))[:6]


def _missing_features(row: dict[str, Any], labs: list[dict[str, str]], vitals: list[dict[str, str]]) -> list[str]:
    missing: list[str] = []
    if not vitals:
        missing.append("结构化生命体征")
    elif not any(item.get("pairing_status") == "paired" for item in vitals):
        missing.append("生命体征名称与数值无法可靠配对")
    if _has_multiple_values(row.get("超声-射血分数")):
        missing.append("LVEF 多值记录无法可靠确定时点")
    elif _single_number(row.get("超声-射血分数")) is None:
        missing.append("结构化 LVEF")
    for label, terms in IMPORTANT_LABS[:4]:
        matches = [item for item in labs if _lab_match(item["item"], terms)]
        if not matches:
            missing.append(label)
        elif not any(item.get("pairing_status") == "paired" for item in matches):
            missing.append(f"{label}名称与结果无法可靠配对")
    return missing[:5]


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _load_row(table: str, rowid: int) -> dict[str, Any] | None:
    with connect_readonly() as connection:
        row = connection.execute(
            f"SELECT rowid AS _rowid, * FROM {quote_identifier(table)} WHERE rowid = ?",
            (rowid,),
        ).fetchone()
    return _row_dict(row) if row else None


def _display_id(table: str, row: dict[str, Any]) -> str:
    return make_display_id(table, int(row["_rowid"]), row.get("regno"), row.get("admno"))


def _row_to_summary(table: str, row: dict[str, Any]) -> dict[str, Any]:
    label = int(_number(row.get("label")) or (1 if table == "break_data" else 0))
    labs = _extract_labs(row)
    vitals = _extract_vitals(row)
    signals = _feature_signals(row, labs, vitals)
    missing = _missing_features(row, labs, vitals)
    if label == 1:
        level = "HIGH"
        status = "回顾性重点复核"
    elif len(signals) >= 2:
        level = "MEDIUM"
        status = "特征异常待复核"
    else:
        level = "LOW"
        status = "常规数据复核"
    age = _number(row.get("年龄"))
    ef = _single_number(row.get("超声-射血分数"))
    factors = signals or (["关键结构化特征缺失"] if len(missing) >= 4 else ["当前未检出预设异常信号"])
    return {
        "patient_id": _display_id(table, row),
        "age": int(age) if age is not None else "—",
        "gender": _clean_text(row.get("性别"), 10) or "—",
        "ward": _ward(row),
        "diagnosis": _diagnosis(row),
        "cohort_label": label,
        "cohort_group": COHORT_LABELS[label],
        "risk_level": level,
        "signal_count": len(signals),
        "factors": factors[:4],
        "missing_count": len(missing),
        "lvef": ef,
        "updated_at": "最新数据",
        "status": status,
        "owner": "未分配",
        "_priority": (100 if label == 1 else 0) + len(signals) * 10 - len(missing),
    }


def _fetch_rows(table: str, limit: int, where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    table_q = quote_identifier(table)
    sql = f"SELECT rowid AS _rowid, * FROM {table_q}"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY CAST(\"年龄\" AS REAL) DESC, rowid DESC LIMIT ?"
    with connect_readonly() as connection:
        rows = connection.execute(sql, (*params, max(1, int(limit)))).fetchall()
    return [_row_dict(row) for row in rows]


def _patient_tables(cohort_label: int | None) -> tuple[str, ...]:
    if cohort_label == 1:
        return ("break_data",)
    if cohort_label == 0:
        return ("nonbreak_data",)
    return SOURCE_TABLES


def _diagnosis_search(
    connection: Any,
    table: str,
    keyword: str,
) -> tuple[str, tuple[str, ...]]:
    if not keyword:
        return "", ()
    available = {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    }
    diagnosis_columns = (
        "入院诊断",
        "首页门急诊诊断",
        "急诊-主诊断名称",
        "诊断名称",
        "门诊-诊断",
        "初步诊断",
    )
    columns = [column for column in diagnosis_columns if column in available]
    if not columns:
        return "0 = 1", ()
    where = " OR ".join(f"COALESCE({quote_identifier(column)}, '') LIKE ?" for column in columns)
    return f"({where})", tuple(f"%{keyword}%" for _ in columns)


def _fetch_rows_by_ids(connection: Any, table: str, rowids: list[int]) -> dict[int, dict[str, Any]]:
    if not rowids:
        return {}
    placeholders = ", ".join("?" for _ in rowids)
    rows = connection.execute(
        f"SELECT rowid AS _rowid, * FROM {quote_identifier(table)} WHERE rowid IN ({placeholders})",
        tuple(rowids),
    ).fetchall()
    return {int(row["_rowid"]): _row_dict(row) for row in rows}


def get_patient_page(
    page: int = 1,
    page_size: int = 100,
    cohort_label: int | None = None,
    keyword: str = "",
) -> dict[str, Any]:
    """Return one de-identified patient page while counting the complete database selection."""
    page_size = max(20, min(int(page_size), 200))
    requested_page = max(1, int(page))
    keyword = _clean_text(keyword, limit=80)
    tables = _patient_tables(cohort_label)

    # A display ID is derived from a protected database location, so resolve it
    # directly instead of scanning diagnosis text or exposing source identifiers.
    if keyword.upper().startswith("XD-"):
        location = resolve_display_id(keyword)
        items: list[dict[str, Any]] = []
        if location and location[0] in tables:
            row = _load_row(*location)
            if row:
                summary = _row_to_summary(location[0], row)
                summary.pop("_priority", None)
                items.append(summary)
        total = len(items)
        return {
            "items": items,
            "total": total,
            "page": 1,
            "page_size": page_size,
            "total_pages": 1,
        }

    with connect_readonly() as connection:
        search_by_table = {
            table: _diagnosis_search(connection, table, keyword)
            for table in tables
        }
        total = 0
        for table in tables:
            where, params = search_by_table[table]
            sql = f"SELECT COUNT(*) FROM {quote_identifier(table)}"
            if where:
                sql += f" WHERE {where}"
            total += int(connection.execute(sql, params).fetchone()[0])

        total_pages = max(1, math.ceil(total / page_size))
        current_page = min(requested_page, total_pages)
        offset = (current_page - 1) * page_size
        locations: list[tuple[str, int]] = []

        if total:
            union_parts: list[str] = []
            union_params: list[Any] = []
            for table in tables:
                where, params = search_by_table[table]
                part = (
                    f"SELECT '{table}' AS source_table, rowid AS source_rowid "
                    f"FROM {quote_identifier(table)}"
                )
                if where:
                    part += f" WHERE {where}"
                union_parts.append(part)
                union_params.extend(params)
            location_sql = (
                "SELECT source_table, source_rowid FROM ("
                + " UNION ALL ".join(union_parts)
                + ") ORDER BY "
                "((source_rowid * 1103515245 + CASE source_table "
                "WHEN 'break_data' THEN 12345 ELSE 67890 END) % 2147483647), "
                "source_table, source_rowid LIMIT ? OFFSET ?"
            )
            union_params.extend((page_size, offset))
            locations = [
                (str(row["source_table"]), int(row["source_rowid"]))
                for row in connection.execute(location_sql, tuple(union_params)).fetchall()
            ]

        rows_by_location: dict[tuple[str, int], dict[str, Any]] = {}
        for table in tables:
            rowids = [rowid for source_table, rowid in locations if source_table == table]
            table_rows = _fetch_rows_by_ids(connection, table, rowids)
            for rowid, row in table_rows.items():
                rows_by_location[(table, rowid)] = row

    items = [
        _row_to_summary(table, rows_by_location[(table, rowid)])
        for table, rowid in locations
        if (table, rowid) in rows_by_location
    ]
    for item in items:
        item.pop("_priority", None)
    return {
        "items": items,
        "total": total,
        "page": current_page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_patients(limit: int = 200, cohort_label: int | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    if cohort_label == 1:
        sources = [("break_data", limit)]
    elif cohort_label == 0:
        sources = [("nonbreak_data", limit)]
    else:
        positive = min(max(limit // 2, 1), limit)
        sources = [("break_data", positive), ("nonbreak_data", limit - positive)]
    patients = [
        _row_to_summary(table, row)
        for table, table_limit in sources
        if table_limit > 0
        for row in _fetch_rows(table, table_limit)
    ]
    patients.sort(key=lambda item: (item["_priority"], item["age"] if isinstance(item["age"], int) else -1), reverse=True)
    for patient in patients:
        patient.pop("_priority", None)
    return patients[:limit]


def _screen_focus_mode(focus: str) -> str:
    normalized = str(focus or "").lower()
    if any(term in normalized for term in ("缺失", "补充", "数据缺口", "待核对")):
        return "missing"
    if any(term in normalized for term in ("血压", "心率", "脉搏", "血流动力学", "休克")):
        return "hemodynamic"
    if any(term in normalized for term in ("超声", "lvef", "射血分数", "心包")):
        return "echo"
    if any(term in normalized for term in ("凝血", "d-二聚体")):
        return "coagulation"
    if any(term in normalized for term in ("肌钙蛋白", "心肌损伤", "ck-mb")):
        return "cardiac_lab"
    if any(term in normalized for term in ("肌酐", "肾功能")):
        return "renal"
    if any(term in normalized for term in ("炎症", "白细胞", "c反应蛋白", "crp")):
        return "inflammation"
    if any(term in normalized for term in ("高龄", "年龄", "老年")):
        return "elderly"
    return "general"


def _matches_screen_focus(row: dict[str, Any], mode: str, focus: str) -> bool:
    factors = " ".join(str(value) for value in row.get("factors", []))
    normalized = str(focus or "").lower()
    if mode == "hemodynamic":
        return any(term in factors for term in ("收缩压", "心率", "脉搏"))
    if mode == "echo":
        return row.get("lvef") is not None or "LVEF" in factors
    if mode == "coagulation":
        return "D-二聚体" in factors
    if mode == "cardiac_lab":
        return any(term in factors for term in ("心肌肌钙蛋白", "肌钙蛋白", "CK-MB"))
    if mode == "renal":
        return "肌酐" in factors
    if mode == "inflammation":
        return any(term in factors for term in ("白细胞", "C反应蛋白", "CRP"))
    if mode == "elderly":
        return "高龄" in factors
    if mode == "missing":
        asks_labs = any(term in normalized for term in ("检验", "肌钙蛋白", "d-二聚体", "肌酐", "炎症"))
        if ("超声" in normalized or "lvef" in normalized) and not asks_labs:
            return row.get("lvef") is None
        return int(row.get("missing_count", 0)) > 0
    return True


def _screen_query(focus: str) -> tuple[str, tuple[str, ...], str]:
    normalized = str(focus or "").lower()
    mode = _screen_focus_mode(focus)
    clauses: list[str] = []
    params: list[str] = []

    def add_like(columns: Iterable[str], terms: Iterable[str]) -> None:
        group: list[str] = []
        for column in columns:
            for term in terms:
                group.append(f"{quote_identifier(column)} LIKE ?")
                params.append(f"%{term}%")
        clauses.append("(" + " OR ".join(group) + ")")

    if mode == "missing":
        clauses.append(
            "(NULLIF(TRIM(\"超声-射血分数\"), '') IS NULL OR "
            "(NULLIF(TRIM(\"检验项名称\"), '') IS NULL AND "
            "NULLIF(TRIM(\"检验项名称_2\"), '') IS NULL AND "
            "NULLIF(TRIM(\"检验项名称_3\"), '') IS NULL))"
        )
    elif mode == "hemodynamic":
        add_like(("项目名称", "体格检查(生命体征、一般情况)"), ("血压", "收缩压", "舒张压", "心率", "脉搏"))
    elif mode == "echo":
        clauses.append(
            "(NULLIF(TRIM(\"超声-射血分数\"), '') IS NOT NULL OR "
            "NULLIF(TRIM(\"超声-超声提示\"), '') IS NOT NULL)"
        )
    elif mode == "coagulation":
        add_like(("检验项名称", "检验项名称_2", "检验项名称_3"), ("D-二聚体", "D-Dimer", "凝血"))
    elif mode == "cardiac_lab":
        add_like(("检验项名称", "检验项名称_2", "检验项名称_3"), ("肌钙蛋白", "CK-MB"))
    elif mode == "renal":
        add_like(("检验项名称", "检验项名称_2", "检验项名称_3"), ("肌酐", "eGFR"))
    elif mode == "inflammation":
        add_like(("检验项名称", "检验项名称_2", "检验项名称_3"), ("白细胞", "C反应蛋白", "C-反应蛋白", "CRP"))
    elif mode == "elderly":
        clauses.append("CAST(\"年龄\" AS REAL) >= 75")

    return " AND ".join(clauses), tuple(params), mode


def _fetch_screen_rows(table: str, where: str, params: tuple[str, ...]) -> list[dict[str, Any]]:
    sql = f"SELECT rowid AS _rowid, * FROM {quote_identifier(table)}"
    if where:
        sql += f" WHERE {where}"
    with connect_readonly() as connection:
        return [_row_dict(row) for row in connection.execute(sql, params).fetchall()]


@lru_cache(maxsize=64)
def _patient_screen_cached(
    signature: tuple[int, int, int, int],
    focus: str,
    limit: int,
) -> dict[str, Any]:
    del signature
    where, params, mode = _screen_query(focus)
    candidates: list[dict[str, Any]] = []
    scanned_rows = 0
    matched_rows = 0
    scanned_tables: list[str] = []

    # The transparent priority rule ranks every retrospective break-group row
    # above every nonbreak row. Evaluate the complete break table first; only
    # scan the nonbreak table when fewer than `limit` focused matches exist.
    for table in SOURCE_TABLES:
        rows = _fetch_screen_rows(table, where, params)
        scanned_tables.append(table)
        scanned_rows += len(rows)
        parsed = [_row_to_summary(table, row) for row in rows]
        focused = [row for row in parsed if _matches_screen_focus(row, mode, focus)]
        matched_rows += len(focused)
        candidates.extend(focused)
        if table == "break_data" and len(candidates) >= limit:
            break

    candidates.sort(
        key=lambda row: (
            row["cohort_label"],
            row["signal_count"],
            -row["missing_count"],
            row["age"] if isinstance(row["age"], int) else -1,
        ),
        reverse=True,
    )
    for patient in candidates:
        patient.pop("_priority", None)
    return {
        "focus": focus or "综合复核",
        "mode": mode,
        "items": candidates[:limit],
        "scanned_rows": scanned_rows,
        "matched_rows": matched_rows,
        "scanned_tables": scanned_tables,
        "ranking_complete": True,
        "match_count_complete": len(scanned_tables) == len(SOURCE_TABLES),
        "ranking_rule": "回顾性标签优先，其次按结构化信号、数据缺口和年龄排序",
    }


def get_patient_screen(focus: str, limit: int = 12) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 20))
    return deepcopy(_patient_screen_cached(database_signature(), str(focus or "综合复核"), bounded_limit))


def screen_patients(focus: str, limit: int = 12) -> list[dict[str, Any]]:
    return get_patient_screen(focus, limit)["items"]


def get_patient_summary(patient_id: str) -> dict[str, Any]:
    location = resolve_display_id(patient_id)
    if not location:
        raise KeyError(f"未找到展示编号 {patient_id}")
    row = _load_row(*location)
    if row is None:
        raise KeyError(f"数据库记录已不存在：{patient_id}")
    summary = _row_to_summary(location[0], row)
    summary.pop("_priority", None)
    return summary


def get_break_patient_summary(patient_id: str) -> dict[str, Any]:
    """Return a patient only when the source record belongs to the rupture cohort."""
    summary = get_patient_summary(patient_id)
    if summary.get("cohort_label") != 1:
        raise KeyError(f"患者 {patient_id} 不属于心脏破裂组")
    return summary


def get_patient_detail(patient_id: str) -> dict[str, Any]:
    return deepcopy(_get_patient_detail_cached(patient_id, database_signature()))


@lru_cache(maxsize=512)
def _get_patient_detail_cached(
    patient_id: str,
    signature: tuple[int, int, int, int],
) -> dict[str, Any]:
    del signature
    location = resolve_display_id(patient_id)
    if not location:
        raise KeyError(f"未找到展示编号 {patient_id}")
    table, rowid = location
    row = _load_row(table, rowid)
    if row is None:
        raise KeyError(f"数据库记录已不存在：{patient_id}")
    summary = _row_to_summary(table, row)
    labs = _extract_labs(row)
    vitals = _extract_vitals(row)
    key_labs = _key_lab_rows(labs)
    signals = _feature_signals(row, labs, vitals)
    missing = _missing_features(row, labs, vitals)
    cutoff = row.get("cutoff_time")
    lvef_raw = row.get("超声-射血分数")
    lvef = _single_number(lvef_raw)
    lvef_multiple = _has_multiple_values(lvef_raw)
    lvef_status = (
        "存在多项记录，结果与检查时点无法可靠配对"
        if lvef_multiple
        else "已记录" if lvef is not None else "缺失"
    )
    lvef_pairing = "unavailable" if lvef_multiple else "paired" if lvef is not None else "missing"
    feature_rows = [
        {"name": "年龄", "value": str(summary["age"]), "unit": "岁", "status": "已记录", "source": "基础信息"},
        {"name": "性别", "value": summary["gender"], "unit": "", "status": "已记录", "source": "基础信息"},
        {
            "name": "LVEF",
            "value": _format_number(lvef),
            "unit": "%" if lvef is not None else "",
            "status": lvef_status,
            "source": "超声",
            "pairing_status": lvef_pairing,
        },
    ]
    feature_rows.extend(
        {
            "name": item["display_name"],
            "value": item["value"],
            "unit": item["unit"],
            "status": (
                item["flag"] or "已记录"
                if item.get("pairing_status") == "paired"
                else "仅确认项目存在，结果与单位无法可靠配对"
            ),
            "source": item["source"],
            "pairing_status": item.get("pairing_status", "unavailable"),
        }
        for item in key_labs[:8]
    )

    timeline = _build_timeline(row, summary, vitals, key_labs)
    supporting = [
        {"title": signal, "detail": "由数据库结构化字段按透明规则识别，需结合原始病历复核。", "time": "15天窗口内", "source": SOURCE_NAME}
        for signal in signals
    ]
    if summary["cohort_label"] == 1:
        supporting.insert(
            0,
            {
                "title": "回顾性队列结局：心脏破裂组",
                "detail": "这是数据集已有结局标签，只用于回顾性展示，不是系统对未来风险的预测。",
                "time": "队列标注",
                "source": f"{SOURCE_NAME} / label",
            },
        )
    counter = []
    if summary["cohort_label"] == 0:
        counter.append(
            {
                "title": "回顾性队列结局：非破裂组",
                "detail": "当前记录来自非破裂组；该标签不能排除未来风险，也不能替代临床判断。",
                "time": "队列标注",
                "source": f"{SOURCE_NAME} / label",
            }
        )
    if not signals:
        counter.append(
            {
                "title": "未检出预设结构化异常信号",
                "detail": "仅表示当前已解析字段未触发规则，不代表患者无风险。",
                "time": "当前数据窗口",
                "source": "数据复核规则",
            }
        )
    missing_rows = []
    for name in missing:
        pairing_gap = "无法可靠" in name
        missing_rows.append(
            {
                "title": name,
                "detail": (
                    "原数据库将名称、数值、单位、标记和时间分别聚合，缺少事件级关联键；"
                    "系统不会按列表位置猜测对应关系。"
                    if pairing_gap
                    else "该关键特征在当前结构化记录中不可用。"
                ),
                "time": "当前数据窗口",
                "source": "资料待核对",
            }
        )
    return {
        "profile": {
            "patient_id": summary["patient_id"],
            "age": summary["age"],
            "gender": summary["gender"],
            "diagnosis": summary["diagnosis"],
            "admission_time": _relative_time(_first(row, ("首页入院时间", "就诊日期时间"), 60), cutoff),
            "ward": summary["ward"],
            "stage": "回顾性 15 天数据窗口",
            "monitoring": summary["status"],
            "latest_event": timeline[-1]["title"] if timeline else "当前仅有结构化快照",
            "cohort_group": summary["cohort_group"],
        },
        "review": {
            "level": summary["risk_level"],
            "signal_count": summary["signal_count"],
            "cohort_label": summary["cohort_label"],
            "cohort_group": summary["cohort_group"],
            "updated_at": "最新数据",
            "alert": summary["status"],
            "basis": "回顾性标签 + 结构化特征规则",
            "probability": None,
        },
        "important_features": feature_rows,
        "observations": {"vitals": vitals[:20], "laboratory": key_labs[:16]},
        "timeline": timeline,
        "evidence": {"supporting": supporting, "counter": counter, "missing": missing_rows},
        "handover": (
            f"{summary['patient_id']}，{summary['age']}岁{summary['gender']}，{summary['diagnosis']}。"
            f"回顾性队列为{summary['cohort_group']}；当前解析到 {len(signals)} 个结构化复核信号："
            f"{'、'.join(signals) if signals else '暂无预设异常信号'}。"
            f"缺失关键特征：{'、'.join(missing) if missing else '未发现预设缺口'}。"
            "以上为数据库结构化摘要，不构成预测、诊断或医嘱。"
        ),
    }


def _build_timeline(
    row: dict[str, Any],
    summary: dict[str, Any],
    vitals: list[dict[str, str]],
    labs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    candidates: list[tuple[datetime | None, dict[str, Any]]] = []
    cutoff = row.get("cutoff_time")

    def add(time_column: str, event_type: str, title: str, summary_text: str, source: str) -> None:
        value = row.get(time_column)
        if not _clean_text(value):
            return
        candidates.append(
            (
                _parse_datetime(value),
                {
                    "time": _relative_time(value, cutoff),
                    "type": event_type,
                    "title": title,
                    "summary": _clean_text(summary_text, 160),
                    "source": source,
                },
            )
        )

    add("首页入院时间", "入院记录", "入院记录进入窗口", summary["diagnosis"], "入院信息")
    add("急诊-就诊时间", "入院记录", "急诊就诊记录", _first(row, ("急诊-主诊断名称", "急诊-主要就诊原因"), 120) or "急诊结构化记录可用", "急诊记录")
    add("检查日期", "病历记录", "检查结果记录", _first(row, ("检查名称", "检查结果"), 120) or "检查记录可用", "检查记录")
    echo_time = row.get("超声-检查时间")
    echo_lvef = _single_number(row.get("超声-射血分数"))
    echo_pairing_reliable = (
        _clean_text(echo_time)
        and not _has_multiple_values(echo_time)
        and echo_lvef is not None
        and not _has_multiple_values(row.get("超声-射血分数"))
    )
    if echo_pairing_reliable:
        add("超声-检查时间", "心脏超声", "超声检查记录", f"LVEF：{_format_number(echo_lvef)}%", "超声")
    elif _clean_text(echo_time) or _clean_text(row.get("超声-射血分数")):
        candidates.append(
            (
                None,
                {
                    "time": "15天窗口内",
                    "type": "资料待核对",
                    "title": "超声记录（LVEF待核对）",
                    "summary": "存在超声时间或 LVEF 记录，但多项结果与检查时点无法可靠配对。",
                    "source": "超声 / 资料待核对",
                },
            )
        )
    add("介入-手术日期", "介入治疗", "介入手术记录", _first(row, ("介入-手术名称", "介入-结论"), 120) or "介入记录可用", "介入手术")
    add("诊断时间", "病历记录", "诊断记录", _first(row, ("诊断名称",), 120) or summary["diagnosis"], "诊断记录")
    paired_vitals = [item for item in vitals if item.get("pairing_status") == "paired"]
    if paired_vitals:
        candidates.append(
            (
                _parse_datetime(row.get("测量时间")),
                {
                    "time": _relative_time(row.get("测量时间"), cutoff),
                    "type": "生命体征",
                    "title": "生命体征结构化记录",
                    "summary": "；".join(f"{item['item']} {item['value']}" for item in paired_vitals[:4]),
                    "source": "护理测量",
                },
            )
        )
    elif vitals:
        candidates.append(
            (
                None,
                {
                    "time": "15天窗口内",
                    "type": "资料待核对",
                    "title": "生命体征项目记录（数值待核对）",
                    "summary": "可见项目：" + "、".join(item["item"] for item in vitals[:6])
                    + "；名称、数值与时间无法可靠配对。",
                    "source": "护理测量 / 资料待核对",
                },
            )
        )
    paired_labs = [item for item in labs if item.get("pairing_status") == "paired"]
    if paired_labs:
        candidates.append(
            (
                _parse_datetime(row.get("采集时间")),
                {
                    "time": paired_labs[0]["time"],
                    "type": "检验结果",
                    "title": "关键检验结果记录",
                    "summary": "；".join(
                        f"{item['display_name']} {item['value']} {item['unit']}".strip()
                        for item in paired_labs[:4]
                    ),
                    "source": "检验结果",
                },
            )
        )
    elif labs:
        candidates.append(
            (
                None,
                {
                    "time": "15天窗口内",
                    "type": "资料待核对",
                    "title": "关键检验项目记录（结果待核对）",
                    "summary": "可见项目：" + "、".join(item["display_name"] for item in labs[:6])
                    + "；项目、结果、单位与采集时间无法可靠配对。",
                    "source": "检验结果 / 资料待核对",
                },
            )
        )
    candidates.sort(key=lambda item: (item[0] is None, item[0] or datetime.max))
    events: list[dict[str, Any]] = []
    for index, (_, event) in enumerate(candidates, 1):
        events.append(
            event
            | {
                "id": f"{summary['patient_id']}-E{index:02d}",
                "raw": "仅展示脱敏后的结构化摘要；原始标识与自由文本不进入前端。",
            }
        )
    return events


def get_patient_timeline(patient_id: str) -> list[dict[str, Any]]:
    return get_patient_detail(patient_id)["timeline"]


def get_patient_review(patient_id: str) -> dict[str, Any]:
    detail = get_patient_detail(patient_id)
    return {
        "current": detail["review"],
        "signals": detail["evidence"]["supporting"],
        "missing": detail["evidence"]["missing"],
    }


def get_cohort_metrics() -> dict[str, Any]:
    return deepcopy(_get_cohort_metrics_cached(database_signature()))


@lru_cache(maxsize=4)
def _get_cohort_metrics_cached(
    signature: tuple[int, int, int, int],
) -> dict[str, Any]:
    del signature
    status = get_database_status()
    rows: list[tuple[int, float | None, str, float | None]] = []
    with connect_readonly() as connection:
        for table, label in (("break_data", 1), ("nonbreak_data", 0)):
            result = connection.execute(
                f"SELECT \"年龄\", \"性别\", \"超声-射血分数\" FROM {quote_identifier(table)}"
            )
            rows.extend((label, _number(age), _clean_text(gender, 10), _number(ef)) for age, gender, ef in result)
    groups: dict[int, dict[str, Any]] = {}
    for label in (1, 0):
        subset = [row for row in rows if row[0] == label]
        ages = [row[1] for row in subset if row[1] is not None]
        efs = [row[3] for row in subset if row[3] is not None]
        groups[label] = {
            "name": COHORT_LABELS[label],
            "count": len(subset),
            "age_median": round(median(ages), 1) if ages else None,
            "female_count": sum(row[2] == "女" for row in subset),
            "male_count": sum(row[2] == "男" for row in subset),
            "lvef_median": round(median(efs), 1) if efs else None,
            "lvef_coverage": round(len(efs) / max(len(subset), 1), 4),
        }
    return {
        "total": status["total"],
        "break_count": status["counts"]["break_data"],
        "nonbreak_count": status["counts"]["nonbreak_data"],
        "break_rate": status["counts"]["break_data"] / max(status["total"], 1),
        "groups": groups,
        "database": status,
        "source": SOURCE_NAME,
        "signature": database_signature(),
    }


def get_feature_coverage() -> list[dict[str, Any]]:
    return deepcopy(_get_feature_coverage_cached(database_signature()))


@lru_cache(maxsize=4)
def _get_feature_coverage_cached(
    signature: tuple[int, int, int, int],
) -> list[dict[str, Any]]:
    del signature
    columns = ("检验项名称", "检验项名称_2", "检验项名称_3")
    selected_features = IMPORTANT_LABS[:7]
    counts_by_table: dict[str, list[int]] = {}
    with connect_readonly() as connection:
        for table in SOURCE_TABLES:
            expressions: list[str] = []
            params: list[str] = []
            for _, terms in selected_features:
                clauses: list[str] = []
                for column in columns:
                    for term in terms:
                        clauses.append(f"{quote_identifier(column)} LIKE ?")
                        params.append(f"%{term}%")
                expressions.append(f"SUM(CASE WHEN {' OR '.join(clauses)} THEN 1 ELSE 0 END)")
            result = connection.execute(
                f"SELECT {', '.join(expressions)} FROM {quote_identifier(table)}",
                tuple(params),
            ).fetchone()
            counts_by_table[table] = [int(value or 0) for value in result]
    rows = []
    for index, (display_name, _) in enumerate(selected_features):
        break_count = counts_by_table["break_data"][index]
        nonbreak_count = counts_by_table["nonbreak_data"][index]
        rows.append(
            {
                "feature": display_name,
                "break_count": break_count,
                "nonbreak_count": nonbreak_count,
                "total_count": break_count + nonbreak_count,
            }
            )
    rows.sort(key=lambda item: item["total_count"], reverse=True)
    return rows


def get_alerts(limit: int = 30) -> list[dict[str, Any]]:
    patients = get_patients(limit=max(1, min(limit, 100)), cohort_label=1)
    return [
        {
            "alert_id": f"AL-{patient['patient_id'][3:]}",
            "time": "15天窗口",
            "patient_id": patient["patient_id"],
            "level": patient["risk_level"],
            "reason": "回顾性破裂组标签；" + "、".join(patient["factors"][:3]),
            "status": "待复核",
            "owner": "未分配",
            "source": f"{SOURCE_NAME} / label",
        }
        for patient in patients[:limit]
    ]


def get_tasks(limit: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "task_id": f"TASK-{index:03d}",
            "title": "复核结构化证据与数据缺口",
            "patient_id": alert["patient_id"],
            "priority": "高",
            "due": "待确认",
            "owner": "未分配",
            "status": "待处理",
        }
        for index, alert in enumerate(get_alerts(limit), 1)
    ]


def get_case_replay(patient_id: str) -> dict[str, Any]:
    detail = get_patient_detail(patient_id)
    events = detail["timeline"] or [
        {
            "id": f"{patient_id}-E01",
            "time": "15天窗口",
            "type": "Medical Record",
            "title": "结构化患者快照",
            "summary": detail["profile"]["diagnosis"],
            "source": SOURCE_NAME,
            "raw": "无可用纵向时间字段。",
        }
    ]
    snapshots = [
        {
            "index": index,
            "time": event["time"],
            "event": event["title"],
            "visible_event_count": index + 1,
            "point_in_time_risk": None,
            "point_in_time_notice": "当前数据库不能从事件序列重建时点风险或累计信号数。",
            "visible_events": events[: index + 1],
        }
        for index, event in enumerate(events)
    ]
    return {"patient_id": patient_id, "detail": detail, "snapshots": snapshots}


def ask_agent(scope_id: str, question: str, history: list[dict] | None = None) -> dict[str, Any]:
    from agent.react_agent import ClinicalReActAgent

    return ClinicalReActAgent().run(scope_id, question, history=history)


def get_data_status() -> dict[str, object]:
    return get_database_status()
