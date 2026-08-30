from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "patient_data.db"
SOURCE_TABLES = ("break_data", "nonbreak_data")


def _streamlit_secret(name: str) -> str:
    try:
        import streamlit as st

        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


def get_database_path() -> Path:
    configured = os.getenv("PATIENT_DB_PATH", "").strip() or _streamlit_secret("PATIENT_DB_PATH")
    path = Path(configured).expanduser() if configured else DEFAULT_DATABASE_PATH
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到患者数据库：{path}")
    return path


def get_display_id_salt() -> str:
    return (
        os.getenv("PATIENT_ID_SALT", "").strip()
        or _streamlit_secret("PATIENT_ID_SALT")
        or "xindun-local-display-id-v1"
    )


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@contextmanager
def connect_readonly() -> Iterator[sqlite3.Connection]:
    path = get_database_path()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def database_signature() -> tuple[int, int, int, int]:
    path = get_database_path()
    stat = path.stat()
    wal_path = Path(f"{path}-wal")
    if wal_path.exists():
        wal_stat = wal_path.stat()
        return stat.st_mtime_ns, stat.st_size, wal_stat.st_mtime_ns, wal_stat.st_size
    return stat.st_mtime_ns, stat.st_size, 0, 0


def make_display_id(table: str, rowid: int, regno: object, admno: object) -> str:
    raw = f"{table}|{rowid}|{regno or ''}|{admno or ''}".encode("utf-8")
    digest = hmac.new(get_display_id_salt().encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"XD-{digest[:10].upper()}"


@lru_cache(maxsize=4)
def _patient_index(
    database_path: str,
    signature: tuple[int, int, int, int],
    salt: str,
) -> dict[str, tuple[str, int]]:
    del signature, salt
    connection = sqlite3.connect(f"file:{Path(database_path).as_posix()}?mode=ro", uri=True)
    try:
        index: dict[str, tuple[str, int]] = {}
        for table in SOURCE_TABLES:
            for rowid, regno, admno in connection.execute(
                f"SELECT rowid, regno, admno FROM {quote_identifier(table)}"
            ):
                index[make_display_id(table, rowid, regno, admno)] = (table, int(rowid))
        return index
    finally:
        connection.close()


def resolve_display_id(patient_id: str) -> tuple[str, int] | None:
    path = get_database_path()
    return _patient_index(
        str(path),
        database_signature(),
        get_display_id_salt(),
    ).get(str(patient_id).strip().upper())


def iter_patient_locations() -> Iterator[tuple[str, int, str]]:
    path = get_database_path()
    index = _patient_index(str(path), database_signature(), get_display_id_salt())
    for display_id, (table, rowid) in index.items():
        yield table, rowid, display_id


@lru_cache(maxsize=4)
def _database_status_cached(
    database_path: str,
    signature: tuple[int, int, int, int],
) -> dict[str, object]:
    del signature
    with connect_readonly() as connection:
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table)}"
            ).fetchone()[0]
            for table in SOURCE_TABLES
        }
    path = Path(database_path)
    return {
        "path": str(path),
        "filename": path.name,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "counts": counts,
        "total": sum(counts.values()),
        "signature": database_signature(),
        "mode": "SQLite read-only",
    }


def get_database_status() -> dict[str, object]:
    path = get_database_path()
    cached = _database_status_cached(str(path), database_signature())
    return cached | {"counts": dict(cached["counts"])}
