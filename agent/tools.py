from __future__ import annotations

from typing import Any, Callable


COHORT_SCOPE_ID = "ALL_PATIENTS"
WORKBOOK_SOURCE = "当前配置的15天窗口工作簿（只读）"

LOCAL_KNOWLEDGE = [
    {
        "id": "CR-KB-001",
        "keywords": ["低血压", "血压", "血流动力学", "休克"],
        "title": "血流动力学信号的复核边界",
        "content": "应结合连续测量、治疗前后状态及数据完整性复核；聚合数据窗口不能替代床旁连续趋势。",
        "source": "本地审核知识条目 / CR-KB-001",
    },
    {
        "id": "CR-KB-002",
        "keywords": ["超声", "射血分数", "心包", "机械并发症"],
        "title": "超声证据复核",
        "content": "LVEF 和超声描述是重要结构化证据，但单次记录或字段缺失不能直接确认或排除机械并发症。",
        "source": "本地审核知识条目 / CR-KB-002",
    },
    {
        "id": "CR-KB-003",
        "keywords": ["肌钙蛋白", "d-二聚体", "bnp", "肌酐", "检验"],
        "title": "检验结果复核",
        "content": "检验结果需要同时核对采集时间、单位、参考范围、异常标记和临床场景，不能孤立解释。",
        "source": "本地审核知识条目 / CR-KB-003",
    },
    {
        "id": "CR-KB-004",
        "keywords": ["治疗", "用药", "手术", "处置", "方案"],
        "title": "治疗相关问题的安全边界",
        "content": "回顾性标签和结构化特征只能帮助定位复核对象，不能据此生成具体医嘱、剂量或手术决定。",
        "source": "本地审核知识条目 / CR-KB-004",
    },
    {
        "id": "CR-KB-005",
        "keywords": ["破裂", "风险", "预测", "模型", "概率"],
        "title": "回顾性标签与预测结果的区别",
        "content": (
            "工作簿 label 是回顾性目标事件标签，不是未来风险概率；"
            "cutoff_time 是数据窗口截止时间，不是预测破裂时间。"
            "只有已配置垂直模型返回的结果才能作为模型预测展示，且仍需临床复核。"
        ),
        "source": "本地审核知识条目 / CR-KB-005",
    },
]


def get_patient_timeline(patient_id: str) -> dict[str, Any]:
    if patient_id == COHORT_SCOPE_ID:
        return {
            "scope": COHORT_SCOPE_ID,
            "window_days": 15,
            "event_count": 0,
            "events": [],
            "notice": "时间轴仅适用于单条就诊记录。",
            "sources": [WORKBOOK_SOURCE],
        }
    from services.workbook_data import get_encounter_detail

    detail = get_encounter_detail(patient_id)
    events = detail["timeline"]
    return {
        "patient_id": patient_id,
        "window_days": 15,
        "event_count": len(events),
        "events": events,
        "notice": "时间来自工作簿真实字段；聚合列表缺少事件级关联键时不会按位置配对。",
        "sources": [WORKBOOK_SOURCE, "工作簿时间字段解析"],
    }


def _feature_payload(records: list[dict], limit: int = 20) -> list[dict[str, str]]:
    return [
        {
            "name": str(item.get("field") or "未命名字段"),
            "value": str(item.get("value") or "暂无记录")[:1200],
            "source": str(item.get("source") or WORKBOOK_SOURCE),
        }
        for item in records[:limit]
    ]


def extract_clinical_features(patient_id: str, focus: str = "") -> dict[str, Any]:
    from services.workbook_data import get_encounter_detail, get_metrics

    if patient_id == COHORT_SCOPE_ID:
        metrics = get_metrics()
        return {
            "scope": COHORT_SCOPE_ID,
            "cohort_features": {
                "patient_count": metrics["patient_count"],
                "encounter_count": metrics["encounter_count"],
                "outcome_distribution": {
                    "label_1": metrics["target_event_count"],
                    "label_0": metrics["encounter_count"] - metrics["target_event_count"],
                },
                "risk_available": False,
                "focus": focus,
            },
            "notice": "队列统计只来自上传工作簿；当前没有可验证的模型风险分层字段。",
            "sources": [WORKBOOK_SOURCE],
        }

    from services.clinical_context import get_patient_clinical_context

    detail = get_encounter_detail(patient_id)
    clinical_context = get_patient_clinical_context(patient_id)
    groups = detail["groups"]
    all_records = [
        *detail["basic"],
        *groups["诊断信息"],
        *groups["检查与检验"],
        *groups["用药与医嘱"],
        *groups["手术信息"],
        *groups["病程记录"],
    ]
    unavailable = [
        {
            "title": "模型风险分层与预测时间不可用",
            "detail": "工作簿本身没有模型输出；风险结果只能来自已配置的预测模型。",
            "source": "字段可用性检查",
        }
    ]
    unavailable.extend(
        {
            "title": str(item.get("code") or "资料质量提示"),
            "detail": str(item.get("message") or "资料需要进一步核对"),
            "source": "预测截点前字段对齐检查",
        }
        for item in clinical_context.get("quality_flags", [])[:10]
    )
    window_evidence: list[dict[str, str]] = []
    for window in clinical_context.get("observation_windows", {}).values():
        for module_rows in window.get("modules", {}).values():
            for item in module_rows:
                window_evidence.append(
                    {
                        "name": str(item.get("evidence_id") or "临床事实"),
                        "value": str(item.get("text") or "暂无记录")[:1200],
                        "source": "预测截点前观察窗",
                    }
                )
    return {
        "patient_id": patient_id,
        "profile": detail["profile"],
        "review": {
            "level": "UNKNOWN",
            "probability": None,
            "notice": "无法判断风险等级。",
        },
        "important_features": window_evidence[:24] or _feature_payload(all_records, 24),
        "features": {
            "supporting": window_evidence[:12],
            "counter": [],
            "missing": unavailable,
        },
        "data_quality": {
            "event_pairing_rule": "仅在时间与内容唯一时直接配对；聚合列表不按位置猜测。",
            "risk_field_available": False,
            "quality_flags": clinical_context.get("quality_flags", []),
        },
        "clinical_context": clinical_context,
        "focus": focus,
        "sources": [
            WORKBOOK_SOURCE,
            "预测截点前时间窗与字段对齐规则",
        ],
    }


def calculate_risk(patient_id: str, focus: str = "") -> dict[str, Any]:
    """Call the uploaded local cardiac-rupture model client for one encounter."""
    del focus
    if patient_id == COHORT_SCOPE_ID:
        return {
            "scope": COHORT_SCOPE_ID,
            "error": "calculate_risk 仅支持当前单条就诊记录，不支持全队列批量预测。",
            "sources": [],
        }
    from agent.risk_model import predict_patient_risk

    return predict_patient_risk(patient_id)


def knowledge_search(query: str) -> dict[str, Any]:
    normalized = str(query or "").strip().lower()
    matches = [
        item
        for item in LOCAL_KNOWLEDGE
        if any(keyword.lower() in normalized for keyword in item["keywords"])
    ]
    selected = matches[:4]
    return {
        "items": [
            {key: value for key, value in item.items() if key != "keywords"}
            for item in selected
        ],
        "notice": (
            "已返回匹配的本地审核知识条目。"
            if selected
            else "本地审核知识库没有匹配条目，不能用其他主题替代。"
        ),
        "sources": [item["source"] for item in selected],
    }


def _patient_parameter() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "前端当前选择的一条就诊记录；实际范围始终由系统覆盖。",
    }


def _focus_parameter() -> dict[str, Any]:
    return {"type": "string", "description": "需要重点核对的问题。"}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_patient_timeline",
            "description": "读取当前单条就诊记录的真实时间字段与结构化事件。",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": _patient_parameter()},
                "required": ["patient_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_clinical_features",
            "description": "读取当前就诊的工作簿结构化字段、字段来源和数据缺口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": _patient_parameter(),
                    "focus": _focus_parameter(),
                },
                "required": ["patient_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_risk",
            "description": "调用已配置的心脏破裂垂直模型；模型不可用时必须返回错误，不能自行推导。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": _patient_parameter(),
                    "focus": _focus_parameter(),
                },
                "required": ["patient_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "搜索本地审核知识和安全边界。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_patient_timeline": get_patient_timeline,
    "extract_clinical_features": extract_clinical_features,
    "calculate_risk": calculate_risk,
    "knowledge_search": knowledge_search,
}

PATIENT_CONTEXT_TOOLS = {
    "get_patient_timeline",
    "extract_clinical_features",
    "calculate_risk",
}


def execute_tool(name: str, arguments: dict[str, Any], scope_id: str) -> dict[str, Any]:
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": f"未知或未启用的工具：{name}", "sources": []}
    clean_arguments = dict(arguments or {})
    if name in PATIENT_CONTEXT_TOOLS:
        clean_arguments["patient_id"] = scope_id
    try:
        return function(**clean_arguments)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "sources": []}
