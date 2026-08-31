from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st


TOOL_LABELS = {
    "extract_clinical_features": "整理临床资料",
    "get_patient_timeline": "读取病程时间轴",
    "calculate_risk": "获取风险预测",
    "knowledge_search": "补充医学知识",
}

THINKING_SECTION_LABELS = {
    "关键依据": "关键依据",
    "关键证据": "关键依据",
    "支持依据": "关键依据",
    "反向依据": "反向依据",
    "反向证据": "反向依据",
    "不确定性": "不确定性",
    "综合判断": "综合判断",
    "临床推理": "综合判断",
}


def inject_react_chat_styles() -> None:
    """Inject styles scoped to ReAct trace markup only."""
    st.html(
        """
        <style>
        .xd-react-timeline {
          margin:.15rem 0 .25rem .2rem;
          padding:.12rem 0 .05rem .1rem;
        }
        .xd-react-step {
          position:relative;
          margin-left:.42rem;
          padding:0 0 .9rem 1.35rem;
        }
        .xd-react-step:last-child { padding-bottom:.12rem; }
        .xd-react-step::before {
          content:""; position:absolute; left:0; top:.38rem;
          width:.48rem; height:.48rem; border-radius:50%;
          background:#287f71; box-shadow:0 0 0 4px #e8f4f1;
        }
        .xd-react-step::after {
          content:""; position:absolute; left:.215rem; top:1.05rem;
          bottom:.08rem; width:1px; background:#d7e5e2;
        }
        .xd-react-step:last-child::after { display:none; }
        .xd-react-step.is-error::before {
          background:#b84d4d; box-shadow:0 0 0 4px #faeaea;
        }
        .xd-react-title { color:#183f42; font-size:.89rem; font-weight:680; }
        .xd-react-detail {
          margin-top:.18rem; color:#667c80; font-size:.79rem; line-height:1.58;
        }
        .xd-react-analysis {
          display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
          gap:.45rem; margin-top:.45rem;
        }
        .xd-react-analysis-item {
          padding:.52rem .62rem; border:1px solid #dfeae8;
          border-radius:.55rem; background:#f8fbfa;
        }
        .xd-react-analysis-label {
          color:#287f71; font-size:.72rem; font-weight:700; margin-bottom:.16rem;
        }
        .xd-react-analysis-text { color:#4e6569; font-size:.77rem; line-height:1.55; }
        .xd-react-answer {
          margin-top:.5rem; padding:.58rem .68rem; border:1px solid #cfe2de;
          border-radius:.55rem; background:#f2f8f6;
        }
        .xd-react-answer-label {
          color:#287f71; font-size:.72rem; font-weight:700; margin-bottom:.2rem;
        }
        .xd-react-answer-text { color:#38575a; font-size:.78rem; line-height:1.58; }
        .xd-react-result-grid {
          display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
          gap:.45rem; margin-bottom:.5rem;
        }
        .xd-react-result-item {
          padding:.5rem .62rem; border:1px solid #d9e7e4;
          border-radius:.5rem; background:#fff;
        }
        .xd-react-result-name { color:#667c80; font-size:.7rem; margin-bottom:.12rem; }
        .xd-react-result-value { color:#183f42; font-size:.9rem; font-weight:720; }
        .xd-react-result-value.is-alert { color:#b43f3f; }
        .xd-react-result-value.is-stable { color:#287f71; }
        .xd-react-result-value.is-uncertain { color:#a76816; }
        .xd-react-source-strip { display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.6rem; }
        .xd-react-source {
          display:inline-flex; padding:.18rem .48rem; border-radius:999px;
          background:#eef6f4; color:#53726e; font-size:.7rem;
        }
        @media (max-width:800px) {
          .xd-react-analysis { grid-template-columns:1fr; }
        }
        </style>
        """
    )


def partial_model_thinking(raw_output: str) -> str:
    raw = str(raw_output or "")
    match = re.search(
        r"<think>\s*(.*?)(?:</think>|$)",
        raw,
        flags=re.I | re.S,
    )
    if match:
        return re.sub(r"</?think>", "", match.group(1), flags=re.I).strip()
    if re.search(r"</think>", raw, flags=re.I):
        return re.split(r"</think>", raw, maxsplit=1, flags=re.I)[0].strip()
    return ""


def partial_model_answer(raw_output: str) -> str:
    raw = str(raw_output or "")
    answer_match = re.search(
        r"<answer>\s*(.*?)(?:</answer>|$)",
        raw,
        flags=re.I | re.S,
    )
    if answer_match:
        return re.sub(r"</?answer>", "", answer_match.group(1), flags=re.I).strip()
    if re.search(r"</think>", raw, flags=re.I):
        return re.split(r"</think>", raw, maxsplit=1, flags=re.I)[1].strip()
    return ""


def thinking_sections(thinking: str) -> list[tuple[str, str]]:
    clean = re.sub(r"</?think>", "", str(thinking or ""), flags=re.I).strip()
    if not clean:
        return []
    aliases = "|".join(
        sorted((re.escape(key) for key in THINKING_SECTION_LABELS), key=len, reverse=True)
    )
    pattern = re.compile(rf"(?:^|[\n。；])\s*({aliases})\s*(?:[:：]|是|为)?", re.I)
    matches = list(pattern.finditer(clean))
    if not matches:
        return [("模型分析", clean)]

    sections: list[tuple[str, str]] = []
    prefix = clean[: matches[0].start()].strip(" \n。；：:")
    if prefix:
        sections.append(("模型分析", prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
        content = clean[match.end() : end].strip(" \n。；：:")
        if content:
            label = THINKING_SECTION_LABELS.get(match.group(1), match.group(1))
            sections.append((label, content))
    return sections or [("模型分析", clean)]


def _thinking_html(thinking: str) -> str:
    blocks = []
    for label, content in thinking_sections(thinking):
        blocks.append(
            "<div class='xd-react-analysis-item'>"
            f"<div class='xd-react-analysis-label'>{html.escape(label)}</div>"
            f"<div class='xd-react-analysis-text'>{html.escape(content).replace(chr(10), '<br>')}</div>"
            "</div>"
        )
    return f"<div class='xd-react-analysis'>{''.join(blocks)}</div>" if blocks else ""


def _risk_result_html(fields: dict[str, Any], answer: str) -> str:
    rupture = str(fields.get("rupture_judgment") or "").strip()
    urgency = str(fields.get("current_urgency") or "").strip()
    evidence = str(fields.get("core_evidence") or fields.get("explanation") or "").strip()
    if not (rupture or urgency or evidence):
        return (
            "<div class='xd-react-answer'>"
            "<div class='xd-react-answer-label'>预测结果</div>"
            f"<div class='xd-react-answer-text'>{html.escape(answer).replace(chr(10), '<br>')}</div>"
            "</div>"
        ) if answer else ""

    rupture_class = (
        "is-alert" if rupture == "是" else
        "is-stable" if rupture == "否" else
        "is-uncertain"
    )
    urgency_class = "is-alert" if urgency == "危急" else "is-stable"
    result_items = []
    if rupture:
        result_items.append(
            "<div class='xd-react-result-item'>"
            "<div class='xd-react-result-name'>未来14天破裂判断</div>"
            f"<div class='xd-react-result-value {rupture_class}'>{html.escape(rupture)}</div>"
            "</div>"
        )
    if urgency:
        result_items.append(
            "<div class='xd-react-result-item'>"
            "<div class='xd-react-result-name'>当前危急度</div>"
            f"<div class='xd-react-result-value {urgency_class}'>{html.escape(urgency)}</div>"
            "</div>"
        )
    evidence_html = (
        "<div class='xd-react-answer-label'>核心依据</div>"
        f"<div class='xd-react-answer-text'>{html.escape(evidence).replace(chr(10), '<br>')}</div>"
        if evidence else ""
    )
    return (
        "<div class='xd-react-answer'>"
        f"<div class='xd-react-result-grid'>{''.join(result_items)}</div>"
        f"{evidence_html}</div>"
    )


def timeline_html(steps: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for step in steps:
        title = html.escape(str(step.get("title") or "读取资料"))
        thinking = str(step.get("thinking") or "").strip()
        answer = str(step.get("answer") or "").strip()
        fields = step.get("fields") if isinstance(step.get("fields"), dict) else {}
        detail = str(step.get("detail") or "").strip()
        if thinking or answer:
            body = _thinking_html(thinking)
            body += _risk_result_html(fields, answer)
        else:
            body = (
                f"<div class='xd-react-detail'>{html.escape(detail).replace(chr(10), '<br>')}</div>"
                if detail else ""
            )
        state = " is-error" if step.get("status") == "error" else ""
        rows.append(
            f"<div class='xd-react-step{state}'>"
            f"<div class='xd-react-title'>{title}</div>{body}</div>"
        )
    return f"<div class='xd-react-timeline'>{''.join(rows)}</div>"


def source_label(source: object) -> str:
    value = str(source or "").strip()
    lowered = value.lower()
    if value.startswith("阿里百炼模型") or any(
        name in lowered for name in ("qwen", "deepseek", "glm")
    ):
        return "医学知识说明"
    if "心脏破裂" in value or "cardiac-rupture" in lowered or "gpu" in lowered:
        return "心脏破裂预测模型"
    return value


def source_strip_html(sources: list[object]) -> str:
    labels = list(dict.fromkeys(label for item in sources if (label := source_label(item))))
    if not labels:
        return ""
    chips = "".join(
        f"<span class='xd-react-source'>来源：{html.escape(label)}</span>" for label in labels
    )
    return f"<div class='xd-react-source-strip'>{chips}</div>"


def restored_timeline_steps(message: dict[str, Any]) -> list[dict[str, Any]]:
    reasoning = message.get("reasoning") or {}
    risk_runs = reasoning.get("risk_runs") or []
    risk_run = risk_runs[-1] if risk_runs else {}
    risk_thinking = str((risk_run.get("prediction") or {}).get("thinking") or "").strip()
    risk_answer = str((risk_run.get("prediction") or {}).get("answer") or "").strip()
    risk_fields = (risk_run.get("prediction") or {}).get("fields") or {}
    steps: list[dict[str, Any]] = []
    for item in message.get("react_steps") or []:
        phase = str(item.get("phase") or "")
        if phase == "Reason":
            steps.append({
                "title": str(item.get("title") or "明确查询内容"),
                "detail": str(item.get("summary") or ""),
            })
        elif phase == "Observation":
            tool = str(item.get("tool") or "")
            step = {
                "title": TOOL_LABELS.get(tool, "读取相关资料"),
                "detail": str(item.get("summary") or ""),
                "status": str(item.get("status") or "success"),
            }
            if tool == "calculate_risk":
                if risk_thinking:
                    step["thinking"] = risk_thinking
                step["answer"] = risk_answer
                if isinstance(risk_fields, dict):
                    step["fields"] = risk_fields
            steps.append(step)
        elif phase == "Final":
            steps.append({
                "title": str(item.get("title") or "整理回答"),
                "detail": str(item.get("summary") or ""),
            })
    return steps
