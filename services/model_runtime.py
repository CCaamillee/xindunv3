from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import streamlit as st

from agent.config import get_risk_model_settings
from services.prediction_api import get_prediction_path
from services.workbook_data import get_workbook_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_batch_status_path() -> Path:
    return get_prediction_path().with_suffix(".status.json")


def get_batch_log_path() -> Path:
    return get_prediction_path().with_suffix(".log")


def load_batch_status() -> dict[str, Any]:
    path = get_batch_status_path()
    if not path.is_file():
        return {
            "state": "idle",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "message": "尚未启动该工作簿的批量预测。",
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "error",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "message": "批量预测状态文件无法读取。",
        }
    return value if isinstance(value, dict) else {"state": "error"}


@st.cache_data(ttl="10s", max_entries=4, show_spinner=False)
def check_risk_model_health() -> dict[str, Any]:
    """Check OpenAI-compatible model endpoints without sending patient data."""
    settings = get_risk_model_settings()
    failures: list[str] = []
    for raw_url in settings.urls:
        base_url = str(raw_url).rstrip("/")
        request = Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=1.8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            model_ids = [
                str(item.get("id") or "")
                for item in payload.get("data", [])
                if isinstance(item, dict)
            ]
            return {
                "available": True,
                "endpoint": base_url,
                "model": settings.model,
                "model_reported": settings.model in model_ids,
                "message": (
                    "预测服务在线，目标模型已注册。"
                    if settings.model in model_ids
                    else "预测服务在线；服务未返回目标模型名，启动前请核对 served-model-name。"
                ),
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            failures.append(type(exc).__name__)
    return {
        "available": False,
        "endpoint": "",
        "model": settings.model,
        "model_reported": False,
        "message": (
            "预测模型客户端已配置，但 OpenAI 兼容推理服务尚未在线。"
            f"已检查 {len(settings.urls)} 个配置地址。"
        ),
        "failures": failures,
    }


def launch_batch_prediction(limit: int | None = None) -> dict[str, Any]:
    """Launch the resumable workbook prediction worker in a separate process."""
    health = check_risk_model_health()
    if not health.get("available"):
        return {"started": False, "message": health["message"]}
    current = load_batch_status()
    if current.get("state") in {"starting", "running"}:
        return {"started": False, "message": "批量预测任务已经在运行。"}

    result_path = get_prediction_path()
    status_path = get_batch_status_path()
    log_path = get_batch_log_path()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    status_payload = {
        "state": "starting",
        "total": int(limit or 0),
        "completed": 0,
        "failed": 0,
        "message": "批量预测进程正在启动。",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    status_path.write_text(
        json.dumps(status_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_batch_predictions.py"),
        "--workbook",
        str(get_workbook_path()),
        "--result",
        str(result_path),
        "--status",
        str(status_path),
    ]
    if limit is not None:
        command.extend(["--limit", str(max(0, int(limit)))])
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    return {
        "started": True,
        "pid": process.pid,
        "message": "批量预测任务已启动，可稍后刷新查看进度。",
    }
