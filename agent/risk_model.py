from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable

from agent.config import RiskModelSettings, get_risk_model_settings


RiskEventCallback = Callable[[dict[str, Any]], None]


RISK_SYSTEM_PROMPT = """
你是一位顶尖的心血管专家。请基于提供的精简病历摘要，首先明确给出关于“是否发生心脏破裂”的预测结论，接着分析风险因素及病理机制。

必须遵守：
1. 仅使用输入中的预测截点前资料，不得补充未提供的症状、检查、诊断、治疗或结局。
2. 缺失信息表示未知，不能视为正常或阴性。
3. 回顾性 label、病例分组、患者标识和预测截点后结局不得参与预测。
4. 第一行只能是“【预测结论】 会发生心脏破裂”或“【预测结论】 未发生心脏破裂”，随后再给出依据。
5. 该模型只输出二分类结论和解释，没有经过校准的发生概率，也不输出具体破裂日期或时间窗。
""".strip()


def _emit(callback: RiskEventCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        # UI rendering must never interrupt the model request.
        return


def _normalize_base_url(url: str) -> str:
    normalized = str(url or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)]
    return normalized


def _endpoint_label(url: str) -> str:
    if ":8000" in url:
        return "GPU0"
    if ":8001" in url:
        return "GPU1"
    return "本地推理服务"


def _ordered_urls(patient_id: str, urls: tuple[str, ...]) -> list[str]:
    if len(urls) < 2:
        return list(urls)
    digest = hashlib.sha256(patient_id.encode("utf-8")).digest()
    start = digest[0] % len(urls)
    return [urls[(start + offset) % len(urls)] for offset in range(len(urls))]


def parse_risk_output(raw_output: str) -> dict[str, Any]:
    """Parse the model tags while retaining the exact raw response for audit."""
    raw = str(raw_output or "").strip()
    def last_tag(tag: str) -> tuple[str, bool]:
        lowered = raw.lower()
        close_token = f"</{tag}>"
        open_token = f"<{tag}>"
        close_at = lowered.rfind(close_token)
        if close_at < 0:
            return "", False
        open_at = lowered.rfind(open_token, 0, close_at)
        if open_at < 0:
            return "", False
        value = raw[open_at + len(open_token) : close_at].strip()
        while value.lower().startswith(open_token):
            value = value[len(open_token) :].lstrip()
        return value, True

    thinking, has_think_tag = last_tag("think")
    answer, has_answer_tag = last_tag("answer")
    if not has_answer_tag:
        answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.I | re.S)
        answer = re.sub(r"</?answer>", "", answer, flags=re.I).strip()
    if not thinking:
        before_answer = re.split(r"<answer>", raw, maxsplit=1, flags=re.I)[0]
        thinking = re.sub(r"</?think>", "", before_answer, flags=re.I).strip()

    fields: dict[str, str] = {}
    for key in (
        "rupture_label",
        "rupture_time_window",
        "evidence_confidence",
        "explanation",
    ):
        match = re.search(rf"(?im)^\s*{re.escape(key)}\s*[:：]\s*(.+?)\s*$", answer)
        if match:
            fields[key] = match.group(1).strip()

    # The uploaded fine-tuned Qwen model was trained with a binary conclusion
    # header instead of structured rupture_* fields. Parse that exact contract
    # without inferring a probability, confidence score, or event time.
    if "rupture_label" not in fields:
        if "未发生心脏破裂" in answer:
            fields["rupture_label"] = "0"
        elif "会发生心脏破裂" in answer:
            fields["rupture_label"] = "1"
    if "explanation" not in fields and fields.get("rupture_label") in {"0", "1"}:
        explanation = re.sub(
            r"【预测结论】\s*(?:会发生|未发生)心脏破裂",
            "",
            answer,
            count=1,
        ).strip(" \n\r：:")
        fields["explanation"] = explanation

    return {
        "thinking": thinking,
        "answer": answer,
        "fields": fields,
        "has_think_tag": has_think_tag,
        "has_answer_tag": has_answer_tag,
        "raw_output": raw,
    }


class CardiacRuptureRiskModel:
    """Streaming client for the two local cardiac-rupture inference servers."""

    def __init__(
        self,
        settings: RiskModelSettings | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings or get_risk_model_settings()
        self._client_factory = client_factory

    def _client(self, base_url: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(base_url=base_url)
        from openai import OpenAI

        return OpenAI(
            api_key=self.settings.api_key,
            base_url=base_url,
            timeout=self.settings.timeout_seconds,
            max_retries=0,
        )

    def _stream_one(
        self,
        base_url: str,
        clinical_text: str,
        callback: RiskEventCallback | None,
    ) -> tuple[str, str]:
        label = _endpoint_label(base_url)
        _emit(callback, {"type": "risk_start", "endpoint": label})
        response = self._client(base_url).chat.completions.create(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": RISK_SYSTEM_PROMPT},
                {"role": "user", "content": clinical_text},
            ],
            temperature=0.1,
            max_tokens=self.settings.max_tokens,
            stream=True,
        )

        # Test doubles and some OpenAI-compatible servers may ignore stream=True.
        if hasattr(response, "choices"):
            message = response.choices[0].message
            reasoning = str(getattr(message, "reasoning_content", "") or "")
            content = str(message.content or "")
            if reasoning:
                _emit(callback, {"type": "risk_think_delta", "delta": reasoning})
                content = f"<think>{reasoning}</think>{content}"
            if content:
                _emit(
                    callback,
                    {"type": "risk_delta", "delta": content},
                )
            return content, label

        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", "") if delta is not None else ""
            reasoning = (
                getattr(delta, "reasoning_content", "") if delta is not None else ""
            )
            if reasoning:
                text = str(reasoning)
                reasoning_chunks.append(text)
                _emit(callback, {"type": "risk_think_delta", "delta": text})
            if not content:
                continue
            text = str(content)
            content_chunks.append(text)
            _emit(callback, {"type": "risk_delta", "delta": text})
        content_text = "".join(content_chunks)
        reasoning_text = "".join(reasoning_chunks)
        raw_text = (
            f"<think>{reasoning_text}</think>{content_text}"
            if reasoning_text
            else content_text
        )
        return raw_text, label

    def predict(
        self,
        patient_id: str,
        callback: RiskEventCallback | None = None,
    ) -> dict[str, Any]:
        """Load one encounter's current clinical context and run prediction."""
        if not self.settings.is_configured:
            return {
                "error": "心脏破裂垂直模型尚未配置",
                "patient_id": patient_id,
                "sources": [],
            }
        if patient_id == "ALL_PATIENTS":
            return {
                "error": "calculate_risk 仅支持当前单个患者，不支持全队列批量预测",
                "patient_id": patient_id,
                "sources": [],
            }

        from services.clinical_context import get_patient_clinical_context

        prediction_input = get_patient_clinical_context(patient_id)
        return self.predict_with_context(
            patient_id,
            prediction_input,
            callback=callback,
        )

    def predict_with_context(
        self,
        patient_id: str,
        prediction_input: dict[str, Any],
        callback: RiskEventCallback | None = None,
    ) -> dict[str, Any]:
        """Run prediction from an already loaded, pre-cutoff clinical context."""
        if not self.settings.is_configured:
            return {
                "error": "心脏破裂垂直模型尚未配置",
                "patient_id": patient_id,
                "sources": [],
            }
        if patient_id == "ALL_PATIENTS":
            return {
                "error": "calculate_risk 仅支持当前单个患者，不支持全队列批量预测",
                "patient_id": patient_id,
                "sources": [],
            }
        if prediction_input.get("error"):
            return prediction_input | {"sources": prediction_input.get("sources", [])}
        clinical_text = str(prediction_input.get("clinical_input") or "").strip()
        if not clinical_text:
            return {
                "error": "患者资料中没有可供模型使用的 clinical_input",
                "patient_id": patient_id,
                "prediction_input": prediction_input,
                "sources": prediction_input.get("sources", []),
            }
        started_at = time.monotonic()
        failures: list[dict[str, str]] = []

        for raw_url in _ordered_urls(patient_id, self.settings.urls):
            base_url = _normalize_base_url(raw_url)
            label = _endpoint_label(base_url)
            try:
                raw_output, used_endpoint = self._stream_one(
                    base_url,
                    clinical_text,
                    callback,
                )
                parsed = parse_risk_output(raw_output)
                if not parsed["answer"] or parsed["fields"].get("rupture_label") not in {"0", "1"}:
                    raise ValueError("模型未返回可解析的心脏破裂二分类结论")
                duration = round(time.monotonic() - started_at, 2)
                actual_source = "心脏破裂预测模型"
                result = {
                    "patient_id": patient_id,
                    "available": True,
                    "status": "completed",
                    "model": self.settings.model,
                    "endpoint": used_endpoint,
                    "duration_seconds": duration,
                    "prediction_input": prediction_input,
                    "prediction": parsed,
                    "failed_endpoints": failures,
                    "notice": (
                        "这是预测模型基于预测截点前资料生成的辅助预测，"
                        "不是确诊、概率校准结果或处置建议，需由医生结合完整病历复核。"
                    ),
                    "sources": [*prediction_input.get("sources", []), actual_source],
                }
                _emit(
                    callback,
                    {
                        "type": "risk_complete",
                        "duration_seconds": duration,
                        "thinking": parsed["thinking"],
                        "answer": parsed["answer"],
                    },
                )
                return result
            except Exception as exc:
                failure = {"endpoint": label, "error": type(exc).__name__}
                failures.append(failure)
                _emit(callback, {"type": "risk_retry", **failure})

        duration = round(time.monotonic() - started_at, 2)
        return {
            "error": "两个本地心脏破裂模型服务均调用失败",
            "patient_id": patient_id,
            "duration_seconds": duration,
            "failed_endpoints": failures,
            "prediction_input": prediction_input,
            "sources": prediction_input.get("sources", []),
        }


def predict_patient_risk(
    patient_id: str,
    callback: RiskEventCallback | None = None,
) -> dict[str, Any]:
    return CardiacRuptureRiskModel().predict(patient_id, callback=callback)
