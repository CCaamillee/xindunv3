from __future__ import annotations

import json
import unittest

from agent.config import AgentSettings
from agent.react_agent import ClinicalReActAgent, _requires_risk_prediction


class _Function:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = json.dumps(arguments, ensure_ascii=False)


class _ToolCall:
    def __init__(self, name: str) -> None:
        self.id = "call-risk"
        self.type = "function"
        self.function = _Function(name, {"patient_id": "IGNORED"})

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }


class _Message:
    def __init__(self, content: str = "", tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Message | None = None, delta: object | None = None) -> None:
        self.message = message
        self.delta = delta


class _Completion:
    def __init__(self, message: _Message) -> None:
        self.choices = [_Choice(message=message)]


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content


class _Chunk:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(delta=_Delta(content))]


class _Completions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Completion(_Message(tool_calls=[_ToolCall("calculate_risk")]))
        if self.calls == 2:
            return _Completion(_Message(content="工具充分，可以生成最终回答。"))
        return iter([_Chunk("最终预测"), _Chunk("结果。")])


class _Client:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _fake_risk_predictor(patient_id: str, callback=None) -> dict:
    if callback:
        callback({"type": "risk_start", "endpoint": "GPU0"})
        callback({"type": "risk_delta", "delta": "<think>合成推理</think>"})
        callback({"type": "risk_delta", "delta": "<answer>合成预测</answer>"})
    return {
        "patient_id": patient_id,
        "available": True,
        "endpoint": "GPU0",
        "duration_seconds": 1.2,
        "prediction": {
            "thinking": "合成推理",
            "answer": "合成预测",
            "raw_output": "<think>合成推理</think><answer>合成预测</answer>",
        },
        "prediction_input": {
            "included_sections": ["合成资料"],
            "excluded_fields": ["回顾性标签"],
        },
        "sources": ["合成测试"],
    }


class ReActRiskIntegrationTests(unittest.TestCase):
    def test_risk_intent_is_detected(self) -> None:
        self.assertTrue(_requires_risk_prediction("预测未来14天心脏破裂时间窗"))
        self.assertFalse(_requires_risk_prediction("列出当前临床时间轴"))

    def test_risk_tool_and_final_answer_stream_events(self) -> None:
        events: list[dict] = []
        agent = ClinicalReActAgent(
            settings=AgentSettings(api_key="sk-test-key-long-enough"),
            client=_Client(),
            risk_predictor=_fake_risk_predictor,
        )
        result = agent.run(
            "XD-SYNTHETIC",
            "该患者未来14天会发生心脏破裂吗？",
            event_callback=events.append,
        )

        self.assertEqual(result["content"], "最终预测结果。")
        self.assertEqual(result["trace"][0]["tool"], "calculate_risk")
        self.assertEqual(len(result["reasoning"]["risk_runs"]), 1)
        event_types = [event["type"] for event in events]
        self.assertIn("risk_delta", event_types)
        self.assertIn("final_delta", event_types)
        self.assertEqual(event_types[-1], "complete")


if __name__ == "__main__":
    unittest.main()
