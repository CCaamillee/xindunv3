from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from services.database import get_display_id_salt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERVIEW_PATH = PROJECT_ROOT / "clean_非破裂完整版（15天窗口）.xlsx"
DIAGNOSIS_COLUMNS = (
    "入院诊断",
    "首页门急诊诊断",
    "急诊-主诊断名称",
    "诊断名称",
    "门诊-诊断",
    "初步诊断",
)


def _streamlit_setting(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


def get_overview_path() -> Path:
    configured = os.getenv("PATIENT_OVERVIEW_XLSX", "").strip() or _streamlit_setting(
        "PATIENT_OVERVIEW_XLSX"
    )
    path = Path(configured).expanduser() if configured else DEFAULT_OVERVIEW_PATH
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到患者数据表：{path}")
    return path


def _clean_text(value: object, limit: int = 80) -> str:
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    text = re.sub(r"(?<!\d)\d{6,}(?!\d)", "[已脱敏]", text)
    text = re.sub(
        r"(姓名|身份证号?|住院号|病案号|登记号|手机号|电话)\s*[:：]?\s*[^;，,。 ]+",
        r"\1：[已脱敏]",
        text,
    )
    return text[:limit]


def _first_value(row: pd.Series, columns: tuple[str, ...], limit: int = 80) -> str:
    for column in columns:
        if column not in row.index:
            continue
        text = _clean_text(row.get(column), limit=limit)
        if text:
            return text.split(";")[0].strip()
    return ""


def _number(value: object) -> float | None:
    text = _clean_text(value, limit=120).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    parsed = float(match.group())
    return parsed if 0 <= parsed <= 200 else None


def _lvef_summary(value: object) -> str:
    text = _clean_text(value, limit=240)
    values = [
        float(item)
        for item in re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        if 0 <= float(item) <= 100
    ]
    if not values:
        return "—"
    if len(values) == 1:
        return f"{values[0]:g}%"
    return f"{min(values):g}–{max(values):g}%（多次）"


def _display_id(row: pd.Series, row_number: int, salt: str) -> str:
    def seed_value(value: object) -> str:
        return "" if value is None or pd.isna(value) else str(value).strip()

    regno = seed_value(row.get("regno"))
    admno = seed_value(row.get("admno"))
    fallback = str(row_number) if not (regno or admno) else ""
    raw = f"overview|{regno}|{admno}|{fallback}".encode("utf-8")
    digest = hmac.new(salt.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"XD-{digest[:10].upper()}"


def _is_metadata_row(row: pd.Series) -> bool:
    age = _clean_text(row.get("年龄"), limit=30).lower()
    gender = _clean_text(row.get("性别"), limit=30).lower()
    return age in {"年龄", "patient_age"} or gender in {"性别", "patient_gender"}


@st.cache_data(max_entries=4, show_spinner=False)
def _load_source(
    path_text: str,
    modified_ns: int,
    size: int,
) -> pd.DataFrame:
    del modified_ns, size
    source = pd.read_excel(path_text, sheet_name=0, dtype=str)
    source = source.loc[~source.apply(_is_metadata_row, axis=1)].reset_index(drop=True)
    return source


@st.cache_data(max_entries=4, show_spinner=False)
def _load_overview(
    path_text: str,
    modified_ns: int,
    size: int,
    salt: str,
) -> dict[str, Any]:
    source = _load_source(path_text, modified_ns, size)

    rows: list[dict[str, Any]] = []
    for index, row in source.iterrows():
        age = _number(row.get("年龄"))
        rows.append(
            {
                "患者编号": _display_id(row, index + 1, salt),
                "年龄（岁）": int(age) if age is not None else pd.NA,
                "性别": _clean_text(row.get("性别"), limit=10) or "—",
                "科室": _first_value(row, ("诊断科室",), limit=40) or "—",
                "主要诊断": _first_value(row, DIAGNOSIS_COLUMNS, limit=72) or "—",
                "LVEF": _lvef_summary(row.get("超声-射血分数")),
            }
        )

    table = pd.DataFrame(rows)
    if not table.empty:
        table["年龄（岁）"] = table["年龄（岁）"].astype("Int64")
    return {
        "table": table,
        "total": len(table),
        "source_file": Path(path_text).name,
    }


@st.cache_data(max_entries=4, show_spinner=False)
def _load_patient_source(
    path_text: str,
    modified_ns: int,
    size: int,
    salt: str,
) -> pd.DataFrame:
    source = _load_source(path_text, modified_ns, size).copy()
    source.insert(
        0,
        "_patient_id",
        [_display_id(row, index + 1, salt) for index, row in source.iterrows()],
    )
    return source


def get_patient_overview() -> dict[str, Any]:
    path = get_overview_path()
    stat = path.stat()
    result = _load_overview(
        str(path),
        stat.st_mtime_ns,
        stat.st_size,
        get_display_id_salt(),
    )
    return {
        "table": result["table"].copy(),
        "total": result["total"],
        "source_file": result["source_file"],
    }


def get_patient_source() -> pd.DataFrame:
    """Return the current workbook rows with stable display IDs for server-side use."""
    path = get_overview_path()
    stat = path.stat()
    source = _load_patient_source(
        str(path),
        stat.st_mtime_ns,
        stat.st_size,
        get_display_id_salt(),
    )
    return source.copy()
