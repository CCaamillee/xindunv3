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
3. calculate_risk：调用本机心脏破裂垂直模型，根据当前患者预测截点前的脱敏资料，返回未来14天是否发生心脏破裂、可能时间窗、模型依据与不确定性。
4. knowledge_search：当存在不清楚或不确定的医学知识时，生成清晰、独立的医学知识 query 后调用。

必须遵守：
1. 回答患者相关问题前必须先调用相关工具，不能只根据用户描述或一般医学知识回答。
2. 患者事实只能来自 get_patient_timeline 或 extract_clinical_features 的 Observation；calculate_risk 返回的是模型预测，不得改写为已经发生的事实。
3. knowledge_search 返回的是模型生成的通用医学知识，不是患者事实，也不是文献检索结果。
4. 用户询问是否会发生心脏破裂、预测标签、发生时间窗、风险等级、证据支持度或其他心脏破裂预测指标时，必须先调用 extract_clinical_features 核对预测截点前资料，再调用 calculate_risk；不得自行生成或替代预测模型结果。
5. 工具返回缺失、未知、无法配对或错误时，必须保留该不确定性，不得自行补齐。
6. 不得输出隐藏思维过程或工具未返回的患者事实；患者与就诊标识不发送给模型。
7. 不生成具体医嘱、药物剂量或手术决定；最终判断需要医生结合完整病历复核。
8. 同一条就诊同一轮问答中，每个患者资料工具和 calculate_risk 最多执行一次；已有有效 Observation 后必须复用。
9. calculate_risk 的 <answer> 是预测模型的最终输出，应忠实保留其中的预测标签、时间窗和证据支持度；<think> 只放在可折叠推理记录中，不要混入最终回答。
10. 最终回答应简洁说明结论、依据、缺失信息以及知识来源边界，并明确预测结果仅供临床辅助复核。
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
                "调用心脏破裂垂直模型预测当前患者未来14天是否发生心脏破裂。"
                "当问题涉及心脏破裂预测、风险等级、预测标签、发生时间窗或证据支持度时必须调用。"
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
                "当推理中出现不清楚或不确定的医学知识点时调用。"
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
    blocked = {"patient_id", "regno", "admno", "encounter_key"}
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


def _requires_risk_prediction(question: str) -> bool:
    normalized = str(question or "").lower()
    return any(
        term in normalized
        for term in (
            "心脏破裂",
            "是否破裂",
            "破裂风险",
            "风险预测",
            "预测标签",
            "发生时间窗",
            "预测时间窗",
            "证据支持度",
            "rupture",
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
        return (
            f"心脏破裂垂直模型已在 {result.get('endpoint', '本地服务')} 完成预测"
            f"（{result.get('duration_seconds', 0)} 秒）"
            + (f"：{answer}" if answer else "。")
        )
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
    ) -> str:
        response = client.chat.completions.create(
            model=self.settings.model,
            messages=[
                *messages,
                {
                    "role": "user",
                    "content": (
                        "工具调用已经完成。现在不再调用工具，只基于以上 Observation 生成最终回答。"
                        "如果 calculate_risk 已返回结果，应忠实保留其二分类预测结论和解释；"
                        "模型未提供概率、置信度或具体发生时间时必须明确写暂无，不得补造。"
                        "最后说明该结果仅供临床辅助复核。"
                    ),
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
        risk_required = scope_id != COHORT_SCOPE_ID and _requires_risk_prediction(
            normalized_question
        )
        context_required = scope_id != COHORT_SCOPE_ID and self._risk_predictor is None

        try:
            client = self._get_client()
            for iteration in range(1, self.settings.max_iterations + 1):
                _emit(
                    event_callback,
                    {
                        "type": "phase",
                        "phase": "Reason",
                        "iteration": iteration,
                        "label": "正在判断需要核对的资料",
                    },
                )
                react_steps.append(
                    {
                        "phase": "Reason",
                        "iteration": iteration,
                        "summary": "根据用户问题和已有 Observation 判断下一步；隐藏思维过程不对外输出。",
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
                    if not observations:
                        messages.append(
                            {"role": "assistant", "content": assistant.content or ""}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": "尚未获得任何 Observation。请先调用相关工具，再生成最终回答。",
                            }
                        )
                        continue

                    completed_tools = {
                        item["tool"]
                        for item in observations
                        if not item.get("result", {}).get("error")
                    }
                    if context_required and "extract_clinical_features" not in completed_tools:
                        messages.append(
                            {"role": "assistant", "content": assistant.content or ""}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "尚未获得当前就诊预测截点前的临床事实。"
                                    "请先调用 extract_clinical_features。"
                                ),
                            }
                        )
                        continue
                    if risk_required and "calculate_risk" not in completed_tools:
                        messages.append(
                            {"role": "assistant", "content": assistant.content or ""}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "该问题需要心脏破裂预测指标，但尚未获得 calculate_risk 的有效 Observation。"
                                    "请先调用 calculate_risk。"
                                ),
                            }
                        )
                        continue

                    react_steps.append(
                        {
                            "phase": "Final",
                            "iteration": iteration,
                            "summary": "基于已获得的 Observation 生成最终回答。",
                        }
                    )
                    _emit(
                        event_callback,
                        {
                            "type": "phase",
                            "phase": "Final",
                            "iteration": iteration,
                            "label": "正在生成最终回答",
                        },
                    )
                    final_answer = self._stream_final_answer(
                        client,
                        messages,
                        event_callback,
                    )
                    if not final_answer:
                        final_answer = (assistant.content or "").strip()
                        if final_answer:
                            _emit(
                                event_callback,
                                {"type": "final_delta", "delta": final_answer},
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
                            and not item.get("result", {}).get("error")
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
                            "label": f"正在调用 {tool_name}",
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
