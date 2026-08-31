from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from agent.config import (
    AgentSettings,
    get_agent_settings,
    get_knowledge_timeout_seconds,
)
from agent.tools import COHORT_SCOPE_ID, execute_tool


KNOWLEDGE_MODEL = (
    os.getenv("BAILIAN_KNOWLEDGE_MODEL", "").strip() or "deepseek-v4-flash"
)


SYSTEM_PROMPT = """
你是“智能心盾”的临床 ReAct 助手。你必须严格按照下面的循环工作：

Reason：在内部理解用户问题，判断还缺少什么信息；不得向用户输出隐藏思维过程。
Act：从系统提供的四个工具中选择一个或多个并调用。
Observation：读取工具返回结果，判断证据是否足够。
Repeat or Final：证据不足时继续 Reason → Act → Observation；证据足够时生成最终回答。

工具边界：
1. get_patient_timeline：读取当前就诊记录的真实时间病程时间轴。
2. extract_clinical_features：读取当前患者的结构化特征、支持依据、反向依据和数据缺口。
3. calculate_risk：调用本机心脏破裂垂直模型，根据当前患者预测截点前的脱敏资料，独立返回未来14天破裂判断、当前危急度和核心依据。
4. knowledge_search：仅当你明确无法解释某个具体医学术语或机制时，生成清晰、独立的医学知识 query 后调用。

必须遵守：
1. 回答患者相关问题前必须先调用相关工具，不能只根据用户描述或一般医学知识回答。
2. 患者事实只能来自 get_patient_timeline 或 extract_clinical_features 的 Observation；calculate_risk 返回的是模型预测，不得改写为已经发生的事实。
3. knowledge_search 返回的是模型生成的通用医学知识，不是患者事实，也不是文献检索结果。
4. 用户询问是否会发生心脏破裂、当前是否危急或其他专病预测指标时，必须先调用 extract_clinical_features 核对预测截点前资料，再调用 calculate_risk；不得自行生成或替代预测模型结果。
5. 工具返回缺失、未知、无法配对或错误时，必须保留该不确定性，不得自行补齐。
6. 不得输出隐藏思维过程或工具未返回的患者事实；患者与就诊标识不发送给模型。
7. 不生成具体药物剂量或手术决定；资料存在缺口时，用临床语言指出需要关注或补充的资料。
8. 同一条就诊同一轮问答中，每个患者资料工具和 calculate_risk 最多执行一次；已有有效 Observation 后必须复用。
9. calculate_risk 的 <answer> 是预测模型的最终输出，应忠实保留其中的破裂判断、当前危急度和核心依据；<think> 只放在可折叠推理记录中，不要混入最终回答。
10. 最终回答面向医生，简洁说明结论、主要依据和需关注信息；不要输出模型开发说明、通用免责声明、内部计划或“需要向用户说明”等元话语。
11. 普通的资料缺失、预测不确定性、反向证据、替代病因、鉴别诊断或证据冲突均不构成 knowledge_search 调用理由；每轮最多调用一次。
12. 是否调用工具必须由你结合用户语义和已有 Observation 判断，不依赖固定关键词；决定调用时必须使用 API 原生 function tool call，不得在 content 中输出 Reason、Act、<function_calls>、<invoke> 或工具调用代码。
""".strip()


KNOWLEDGE_SYSTEM_PROMPT = """
你是医学知识解释模型。请回答输入的独立医学知识问题，并遵守：
1. 只提供通用医学知识，不推断任何具体患者的诊断、风险或治疗方案。
2. 对存在争议、依赖指南版本或证据不足的内容明确说明不确定性。
3. 不伪造文献、指南名称、研究数据、概率或数值阈值。
4. 未进行外部文献检索时，必须明确这是模型生成的知识说明，不是文献检索结果。
5. 使用简洁中文回答，并指出哪些内容需要临床医生进一步核查。
""".strip()


def _patient_parameter() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "前端当前选择的一条就诊记录；实际范围始终由系统覆盖。",
    }


REACT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_patient_timeline",
            "description": "读取当前就诊记录的真实时间病程时间轴和结构化事件。",
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
            "description": "读取当前就诊记录的结构化字段、字段来源和数据缺口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": _patient_parameter(),
                    "focus": {
                        "type": "string",
                        "description": "需要重点提取或核对的临床问题。",
                    },
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
            "description": (
                "调用心脏破裂垂直模型，独立判断当前患者未来14天是否发生心脏破裂，"
                "并判断预测截止日当时处于危急还是暂时稳定状态。"
                "当问题涉及心脏破裂预测或当前危急度时调用。"
            ),
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
            "name": "knowledge_search",
            "description": (
                "仅当模型明确不知道某个具体医学术语、机制或知识点时调用。"
                "资料缺失、预测不确定、反向证据和鉴别诊断不应触发。"
                "请生成脱离患者身份信息的独立医学知识查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "清晰、独立且不含患者标识的医学知识问题。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


def _tool_call_payload(call: Any) -> dict[str, Any]:
    if hasattr(call, "model_dump"):
        return call.model_dump(exclude_none=True)
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.function.name,
            "arguments": call.function.arguments,
        },
    }


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    try:
        parsed = json.loads(str(raw_arguments or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_tool_result_for_model(value: Any) -> Any:
    """Remove stable patient/encounter identifiers before a tool result reaches a model."""
    blocked = {
        "patient_id",
        "regno",
        "admno",
        "encounter_key",
        "endpoint",
        "failed_endpoints",
        "model",
    }
    if isinstance(value, dict):
        return {
            key: _sanitize_tool_result_for_model(item)
            for key, item in value.items()
            if str(key).lower() not in blocked
        }
    if isinstance(value, list):
        return [_sanitize_tool_result_for_model(item) for item in value]
    return value


AgentEventCallback = Callable[[dict[str, Any]], None]


def _emit(callback: AgentEventCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        # Rendering failures must not interrupt clinical tool execution.
        return


def _requires_timeline(question: str) -> bool:
    normalized = str(question or "")
    return any(term in normalized for term in ("时间轴", "时间线", "病程变化", "如何变化"))


def _question_analysis_summary(
    question: str,
    observations: list[dict[str, Any]],
) -> tuple[str, str]:
    completed_tools = {
        item.get("tool")
        for item in observations
        if not item.get("result", {}).get("error")
    }
    if "calculate_risk" in completed_tools:
        return (
            "核对预测结果",
            "已取得专病预测结果，正在核对预测结论、主要依据和需要关注的资料缺口。",
        )
    if "extract_clinical_features" in completed_tools:
        return (
            "判断下一步",
            "已整理当前就诊的临床资料，正在判断是否还需病程时间轴、专病预测或医学知识补充。",
        )
    if "get_patient_timeline" in completed_tools:
        return (
            "判断下一步",
            "已读取病程时间轴，正在判断现有记录是否足以回答问题或仍需补充其他资料。",
        )
    if _requires_timeline(question):
        return (
            "明确查询内容",
            "本次需要梳理当前就诊的病程和检查变化，先读取可靠时间轴记录。",
        )
    normalized = str(question or "")
    if any(term in normalized for term in ("资料", "缺口", "质量", "对齐")):
        return (
            "明确查询内容",
            "本次需要检查当前就诊资料的完整性、字段来源和时间对齐情况。",
        )
    return (
        "明确查询内容",
        "正在识别医生关注的是病历事实、病程变化、专病预测还是医学知识，并选择相应资料。",
    )


def _final_step_title(question: str, has_risk_prediction: bool) -> str:
    if has_risk_prediction:
        return "整理预测结果"
    normalized = str(question or "")
    if any(term in normalized for term in ("资料", "缺口", "质量", "对齐")):
        return "整理资料情况"
    if _requires_timeline(question) or any(
        term in normalized for term in ("症状", "生命体征", "循环", "检验", "影像")
    ):
        return "整理病情变化"
    return "整理回答"


def _final_step_summary(
    question: str,
    has_risk_prediction: bool,
    observations: list[dict[str, Any]],
) -> str:
    if has_risk_prediction:
        risk_result = next(
            (
                item.get("result", {})
                for item in observations
                if item.get("tool") == "calculate_risk"
                and not item.get("result", {}).get("error")
            ),
            {},
        )
        prediction = risk_result.get("prediction") or {}
        fields = prediction.get("fields") or {}
        rupture = str(fields.get("rupture_judgment") or "").strip()
        urgency = str(fields.get("current_urgency") or "").strip()
        if rupture and urgency:
            return f"模型破裂判断为{rupture}、当前危急度为{urgency}，正在整理核心依据。"
        label = str(fields.get("rupture_label") or "").strip()
        conclusion = {"1": "会发生心脏破裂", "0": "未发生心脏破裂"}.get(
            label,
            "已取得专病模型结果",
        )
        return f"模型结果为{conclusion}，正在整理核心依据。"
    if _requires_timeline(question):
        return "已读取当前就诊时间轴，正在整理病程和检查变化。"
    if any(term in str(question or "") for term in ("资料", "缺口", "质量", "对齐")):
        return "已读取当前就诊资料，正在整理字段来源和资料缺口。"
    return "已读取当前问题所需资料，正在整理回答。"


def _contains_textual_tool_call(content: str) -> bool:
    """Reject tool protocol text that should have arrived as a native tool call."""
    normalized = str(content or "").lower()
    return any(
        marker in normalized
        for marker in (
            "<function_calls",
            "</function_calls",
            "<invoke ",
            "</invoke>",
        )
    )


def _observation_summary(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"工具调用失败：{result['error']}"
    if tool_name == "get_patient_timeline":
        return f"读取到 {result.get('event_count', 0)} 条真实时间轴事件。"
    if tool_name == "extract_clinical_features":
        if result.get("scope") == COHORT_SCOPE_ID:
            cohort = result.get("cohort_features", {})
            return f"读取到 {cohort.get('patient_count', 0)} 条队列结构化记录。"
        evidence = result.get("features", {})
        return (
            f"读取支持依据 {len(evidence.get('supporting', []))} 条、"
            f"反向依据 {len(evidence.get('counter', []))} 条、"
            f"数据缺口 {len(evidence.get('missing', []))} 条。"
        )
    if tool_name == "calculate_risk":
        prediction = result.get("prediction", {})
        answer = " ".join(str(prediction.get("answer") or "").split())
        if len(answer) > 110:
            answer = answer[:110] + "…"
        return "心脏破裂预测模型已完成本次预测" + (f"：{answer}" if answer else "。")
    if tool_name == "knowledge_search":
        return (
            f"已由 {result.get('model', KNOWLEDGE_MODEL)} 返回通用医学知识说明；"
            "该结果不是患者事实或文献检索结果。"
        )
    return "工具已返回 Observation。"


def _error_response(message: str, trace: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "content": f"当前无法完成 ReAct 推理：{message}",
        "sources": [],
        "simulated": False,
        "mode": "react-unavailable",
        "model": "unavailable",
        "trace": trace or [],
        "react_steps": [],
        "reasoning": {"duration_seconds": 0, "trace": trace or [], "risk_runs": []},
        "task_drafts": [],
        "validation": {"status": "not-completed", "problems": [message]},
    }


class ClinicalReActAgent:
    """A single Reason → Act → Observation loop backed by structured tool calls."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        client: Any | None = None,
        knowledge_model: str | None = None,
        risk_predictor: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings or get_agent_settings()
        self.knowledge_model = (knowledge_model or KNOWLEDGE_MODEL).strip()
        self._client = client
        self._knowledge_client: Any | None = None
        self._risk_predictor = risk_predictor

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
            max_retries=1,
        )
        return self._client

    def _query_medical_knowledge(self, query: str) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        normalized_query = re.sub(
            r"\b(?:regno|admno)\s*[:：=]?\s*[^\s，。；]+",
            "当前就诊",
            normalized_query,
            flags=re.I,
        )[:1000]
        if not normalized_query:
            return {"error": "knowledge_search 缺少有效 query", "sources": []}

        try:
            if self._client is not None:
                knowledge_client = self._client
            else:
                if self._knowledge_client is None:
                    from openai import OpenAI

                    self._knowledge_client = OpenAI(
                        api_key=self.settings.api_key,
                        base_url=self.settings.base_url,
                        timeout=get_knowledge_timeout_seconds(),
                        max_retries=0,
                    )
                knowledge_client = self._knowledge_client
            completion = knowledge_client.chat.completions.create(
                model=self.knowledge_model,
                messages=[
                    {"role": "system", "content": KNOWLEDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": normalized_query},
                ],
                temperature=0.1,
                extra_body={"enable_thinking": False},
            )
            answer = (completion.choices[0].message.content or "").strip()
        except Exception as exc:
            return {
                "error": f"医学知识模型调用失败：{type(exc).__name__}",
                "query": normalized_query,
                "sources": [],
            }

        if not answer:
            return {
                "error": "医学知识模型未返回有效内容",
                "query": normalized_query,
                "sources": [],
            }
        source = f"阿里百炼模型 / {self.knowledge_model}"
        return {
            "query": normalized_query,
            "answer": answer,
            "model": self.knowledge_model,
            "knowledge_type": "model_generated_general_medical_knowledge",
            "patient_fact": False,
            "literature_search_performed": False,
            "notice": "这是模型生成的通用医学知识说明，不是文献检索结果或患者事实。",
            "sources": [source],
        }

    def _act(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        scope_id: str,
        event_callback: AgentEventCallback | None = None,
    ) -> dict[str, Any]:
        if tool_name == "knowledge_search":
            return self._query_medical_knowledge(str(arguments.get("query", "")))
        if tool_name == "calculate_risk":
            if self._risk_predictor is None:
                from agent.risk_model import predict_patient_risk

                predictor = predict_patient_risk
            else:
                predictor = self._risk_predictor
            return predictor(scope_id, callback=event_callback)
        if tool_name in {"get_patient_timeline", "extract_clinical_features"}:
            clean_arguments = dict(arguments)
            clean_arguments["patient_id"] = scope_id
            return execute_tool(tool_name, clean_arguments, scope_id)
        return {"error": f"未知或未启用的工具：{tool_name}", "sources": []}

    def _stream_final_answer(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        callback: AgentEventCallback | None,
        has_risk_prediction: bool,
    ) -> str:
        if has_risk_prediction:
            final_instruction = (
                "工具读取已经结束。只基于以上 Observation，直接向医生给出最终结果。"
                "不要输出内部计划、<think>、Reason、Act、Observation、工具名或“需要向用户说明”等元话语。"
                "使用以下临床表达：\n"
                "破裂判断：原样写出是、否或证据不足。\n"
                "当前危急度：原样写出危急或暂时稳定，不得根据破裂判断自行推断。\n"
                "核心依据：用1至3句话忠实概括工具返回的核心依据。\n"
                "需关注：仅在资料缺失或结论存在明显限制时，用一句话指出医生需要关注或补充的资料。\n"
                "忠实保留 calculate_risk 的两个独立判断，不得自行反转、补造概率、置信度或时间窗。"
                "不要展示 rupture_label 等程序字段。"
                "不要写“辅助输出、并非确诊、概率校准、仅供参考、最终判断需要医生复核”等通用免责声明。"
            )
        else:
            final_instruction = (
                "工具读取已经结束。只基于以上 Observation，直接回答医生的问题。"
                "不要输出内部计划、<think>、Reason、Act、Observation、工具名、数据处理过程、"
                "“需要向用户说明”等元话语，也不要追加通用免责声明或继续提问邀请。"
                "按临床工作场景简洁表达；资料不足时只指出具体缺少的内容。"
            )
        response = client.chat.completions.create(
            model=self.settings.model,
            messages=[
                *messages,
                {
                    "role": "user",
                    "content": final_instruction,
                },
            ],
            temperature=0.1,
            stream=True,
            extra_body={"enable_thinking": False},
        )

        # Test doubles and a few compatible gateways may ignore stream=True.
        if hasattr(response, "choices"):
            content = str(response.choices[0].message.content or "").strip()
            if content:
                _emit(callback, {"type": "final_delta", "delta": content})
            return content

        chunks: list[str] = []
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", "") if delta is not None else ""
            if not content:
                continue
            text = str(content)
            chunks.append(text)
            _emit(callback, {"type": "final_delta", "delta": text})
        return "".join(chunks).strip()

    def run(
        self,
        scope_id: str,
        question: str,
        history: list[dict[str, Any]] | None = None,
        event_callback: AgentEventCallback | None = None,
    ) -> dict[str, Any]:
        normalized_question = str(question or "").strip()
        if not normalized_question:
            return _error_response("问题为空")
        if not self.settings.is_configured:
            return _error_response("尚未配置百炼 API Key，无法执行 Reason 步骤")

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-8:]:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content[:6000]})

        scope_description = (
            "上传工作簿中的全部有效就诊记录"
            if scope_id == COHORT_SCOPE_ID
            else "前端当前选择的一条就诊记录（患者与就诊标识未发送给模型）"
        )
        messages.append(
            {
                "role": "user",
                "content": f"当前查询范围：{scope_description}\n用户问题：{normalized_question}",
            }
        )

        trace: list[dict[str, str]] = []
        react_steps: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        sources: set[str] = set()
        started_at = time.monotonic()

        try:
            client = self._get_client()

            def finalize(
                iteration: int,
            ) -> dict[str, Any]:
                has_risk_prediction = any(
                    item["tool"] == "calculate_risk"
                    and item.get("result", {}).get("prediction")
                    for item in observations
                )
                final_title = _final_step_title(
                    normalized_question,
                    has_risk_prediction,
                )
                final_summary = _final_step_summary(
                    normalized_question,
                    has_risk_prediction,
                    observations,
                )
                react_steps.append(
                    {
                        "phase": "Final",
                        "iteration": iteration,
                        "title": final_title,
                        "summary": final_summary,
                    }
                )
                _emit(
                    event_callback,
                    {
                        "type": "phase",
                        "phase": "Final",
                        "iteration": iteration,
                        "title": final_title,
                        "detail": final_summary,
                    },
                )

                # The no-tool response is only the planner's decision to stop.
                # Always synthesize a separate physician-facing answer so that
                # planning notes never leak into the chat output.
                final_answer = self._stream_final_answer(
                    client,
                    messages,
                    event_callback,
                    has_risk_prediction,
                )
                if not final_answer:
                    return _error_response("模型没有返回最终回答", trace)

                duration_seconds = round(time.monotonic() - started_at, 2)
                risk_runs = [
                    item["result"]
                    for item in observations
                    if item["tool"] == "calculate_risk"
                    and item.get("result", {}).get("prediction")
                ]
                result_payload = {
                    "content": final_answer,
                    "sources": sorted(source for source in sources if source),
                    "simulated": False,
                    "mode": "bailian-react",
                    "model": self.settings.model,
                    "knowledge_model": self.knowledge_model,
                    "trace": trace,
                    "react_steps": react_steps,
                    "reasoning": {
                        "duration_seconds": duration_seconds,
                        "trace": trace,
                        "risk_runs": risk_runs,
                    },
                    "task_drafts": [],
                    "validation": {
                        "status": "completed-after-observation",
                        "observation_count": len(observations),
                        "problems": [],
                    },
                }
                _emit(
                    event_callback,
                    {
                        "type": "complete",
                        "duration_seconds": duration_seconds,
                        "trace": trace,
                    },
                )
                return result_payload

            for iteration in range(1, self.settings.max_iterations + 1):
                reason_title, reason_detail = _question_analysis_summary(
                    normalized_question,
                    observations,
                )
                _emit(
                    event_callback,
                    {
                        "type": "phase",
                        "phase": "Reason",
                        "iteration": iteration,
                        "title": reason_title,
                        "detail": reason_detail,
                    },
                )
                react_steps.append(
                    {
                        "phase": "Reason",
                        "iteration": iteration,
                        "title": reason_title,
                        "summary": reason_detail,
                    }
                )
                completion = client.chat.completions.create(
                    model=self.settings.model,
                    messages=messages,
                    tools=REACT_TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=0.1,
                    extra_body={"enable_thinking": False},
                )
                assistant = completion.choices[0].message
                tool_calls = assistant.tool_calls or []

                if not tool_calls:
                    assistant_content = str(assistant.content or "").strip()
                    if _contains_textual_tool_call(assistant_content):
                        messages.append(
                            {"role": "assistant", "content": assistant_content}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "你刚才把工具协议写进了正文。请重新判断下一步："
                                    "需要工具时使用 API 原生 function tool call；"
                                    "不需要工具时直接给出最终回答，不要输出 Reason、Act 或 XML。"
                                ),
                            }
                        )
                        continue
                    if not assistant_content:
                        messages.append(
                            {"role": "assistant", "content": ""}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "请结合用户问题和已有 Observation 自主判断："
                                    "继续调用所需工具，或直接生成最终回答。"
                                ),
                            }
                        )
                        continue
                    return finalize(iteration)

                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant.content or "",
                        "tool_calls": [_tool_call_payload(call) for call in tool_calls],
                    }
                )

                ordered_calls = sorted(
                    tool_calls,
                    key=lambda call: {
                        "extract_clinical_features": 0,
                        "get_patient_timeline": 1,
                        "calculate_risk": 2,
                        "knowledge_search": 3,
                    }.get(str(call.function.name), 4),
                )
                for call in ordered_calls:
                    tool_name = str(call.function.name)
                    arguments = _parse_arguments(call.function.arguments)
                    cached_result = next(
                        (
                            item["result"]
                            for item in observations
                            if item["tool"] == tool_name
                            and (
                                tool_name == "knowledge_search"
                                or not item.get("result", {}).get("error")
                            )
                        ),
                        None,
                    )
                    if cached_result is not None:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(
                                    _sanitize_tool_result_for_model(cached_result),
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            }
                        )
                        continue
                    react_steps.append(
                        {
                            "phase": "Act",
                            "iteration": iteration,
                            "tool": tool_name,
                        }
                    )
                    _emit(
                        event_callback,
                        {
                            "type": "phase",
                            "phase": "Act",
                            "iteration": iteration,
                            "tool": tool_name,
                            "title": "读取所需资料",
                            "detail": {
                                "extract_clinical_features": "正在整理当前就诊的结构化临床资料、字段来源和资料缺口。",
                                "get_patient_timeline": "正在读取当前就诊的病程时间轴和结构化事件。",
                                "calculate_risk": "正在调用心脏破裂预测模型并读取模型结果。",
                                "knowledge_search": "正在补充一个无法直接解释的医学知识点。",
                            }.get(tool_name, "正在读取相关资料。"),
                        },
                    )
                    result = self._act(
                        tool_name,
                        arguments,
                        scope_id,
                        event_callback=event_callback,
                    )
                    observations.append({"tool": tool_name, "result": result})
                    sources.update(result.get("sources", []))
                    summary = _observation_summary(tool_name, result)
                    status = "error" if result.get("error") else "success"
                    trace.append(
                        {
                            "tool": tool_name,
                            "status": status,
                            "observation": summary,
                        }
                    )
                    _emit(
                        event_callback,
                        {
                            "type": "phase",
                            "phase": "Observation",
                            "iteration": iteration,
                            "tool": tool_name,
                            "status": status,
                            "label": summary,
                        },
                    )
                    react_steps.append(
                        {
                            "phase": "Observation",
                            "iteration": iteration,
                            "tool": tool_name,
                            "status": status,
                            "summary": summary,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                _sanitize_tool_result_for_model(result),
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )

            return _error_response(
                f"达到最大 ReAct 循环次数 {self.settings.max_iterations}，仍未形成最终回答",
                trace,
            )
        except Exception as exc:
            return _error_response(f"ReAct 调用失败：{type(exc).__name__}", trace)
