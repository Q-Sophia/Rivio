from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.harness.artifacts import ArtifactStore
from app.llm.client import LLMClient, LLMStructuredOutputError
from app.llm.config import LLMConfig
from app.llm.provider import (
    LLMProviderResponseError,
    OpenAIResponsesProvider,
    ProviderResult,
    StructuredLLMProvider,
)
from app.llm.structured_output import (
    StructuredJSONParseError,
    parse_structured_json_text,
)
from app.schemas import AgentRole, LLMMode, LLMProvider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class SequenceStructuredProvider(StructuredLLMProvider):
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs) -> ProviderResult:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Offline Provider response 已耗尽")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            raw_output = OpenAIResponsesProvider._decode_json_text(response)
            response_chars = len(response)
        else:
            raw_output = response
            response_chars = len(json.dumps(response, ensure_ascii=False))
        return ProviderResult(
            raw_output=raw_output,
            request_id=f"offline_{len(self.calls)}",
            attempts=1,
            input_tokens=10,
            output_tokens=10,
            metadata={
                "finish_reason": "stop",
                "response_chars": response_chars,
            },
        )


def config() -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model="offline-structured-model",
        mode=LLMMode.LLM,
        base_url="https://offline.invalid/v1",
        api_key_env="OFFLINE_UNUSED_KEY",
        api_style="chat_completions",
        enable_real_calls=False,
        max_retries=0,
    )


def action_artifacts(task_id: str) -> dict[str, list]:
    return {
        "research_task": [
            {
                "id": "researchtask_reliability",
                "task_id": task_id,
            }
        ]
    }


def valid_action_envelope() -> dict[str, Any]:
    return {
        "item": {
            "action": "FINISH",
            "rationale": "当前证据不足，结束本轮研究。",
            "finish_status": "PARTIAL",
        },
        "generated_by": "researcher",
        "output_language": "zh-CN",
    }


def generate_action(
    *,
    store: ArtifactStore,
    task_id: str,
    provider: StructuredLLMProvider,
    node_id: str,
):
    return LLMClient(
        config=config(), store=store, provider=provider
    ).generate_structured(
        task_id=task_id,
        agent_role=AgentRole.RESEARCHER,
        agent_run_id=f"run_{node_id}",
        node_id=node_id,
        context_bundle=None,
        output_schema="ResearchAgentAction",
        prompt_id="offline_reliability",
        prompt_summary="选择下一 Research Agent action。",
        artifacts=action_artifacts(task_id),
    )


def check_parser_variants() -> None:
    expected = {"action": "FINISH"}
    require(
        parse_structured_json_text('{"action":"FINISH"}') == expected,
        "纯 JSON 未通过统一 parser",
    )
    require(
        parse_structured_json_text(
            '```json\n{\n  "action": "FINISH"\n}\n```'
        )
        == expected,
        "markdown JSON 未通过统一 parser",
    )
    require(
        parse_structured_json_text(
            '以下是结果：\n{"action":"FINISH"}\n处理完成。'
        )
        == expected,
        "JSON 前后少量解释文本未通过统一 parser",
    )
    try:
        parse_structured_json_text('[{"action":"FINISH"}]')
    except StructuredJSONParseError:
        pass
    else:
        raise AssertionError("顶层数组被宽松接受为对象")


def check_parse_retry(root: Path) -> None:
    task_id = "task_structured_parse_retry"
    store = ArtifactStore(root / "parse_retry")
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    malformed = '{"item":{"action":"FINISH",},"generated_by":"researcher"}'
    provider = SequenceStructuredProvider(
        [malformed, valid_action_envelope()]
    )
    raw, call, output = generate_action(
        store=store,
        task_id=task_id,
        provider=provider,
        node_id="research_agent_action_parse_retry",
    )
    require(raw["item"]["finish_status"] == "PARTIAL", "parse retry 后未返回合法 Action")
    require(len(provider.calls) == 2, "parse failure 未严格 retry 一次")
    require(
        provider.calls[0]["output_schema"]
        == provider.calls[1]["output_schema"]
        == "ResearchAgentAction",
        "retry 未复用原 schema",
    )
    retry_prompt = provider.calls[1]["prompt_summary"]
    require(
        "只返回" in retry_prompt
        and "合法 JSON" in retry_prompt
        and "不要输出 markdown" in retry_prompt,
        "structured retry prompt 不符合简洁 JSON 约束",
    )
    require(
        call.metadata["structured_output_stage"]
        == "research_agent_action_parse_retry"
        and call.metadata["structured_output_model"]
        == "offline-structured-model"
        and call.metadata["structured_response_chars"] == len(malformed)
        and call.metadata["structured_parse_error"]
        and call.metadata["structured_retry_performed"] is True
        and call.metadata["structured_retry_result"] == "succeeded",
        "parse retry 诊断字段不完整",
    )
    require(
        output.validation_status == "passed"
        and output.metadata["structured_retry_performed"] is True,
        "retry 成功结果未通过统一 validation/trace",
    )


def check_schema_retry(root: Path) -> None:
    task_id = "task_structured_schema_retry"
    store = ArtifactStore(root / "schema_retry")
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    invalid = {
        "item": {"action": "FINISH", "rationale": "结束。"},
        "generated_by": "researcher",
        "output_language": "zh-CN",
    }
    provider = SequenceStructuredProvider([invalid, valid_action_envelope()])
    _raw, call, _output = generate_action(
        store=store,
        task_id=task_id,
        provider=provider,
        node_id="research_agent_action_schema_retry",
    )
    require(len(provider.calls) == 2, "schema failure 未严格 retry 一次")
    require(
        call.metadata["structured_schema_error"]
        and not call.metadata["structured_parse_error"]
        and call.metadata["structured_retry_result"] == "succeeded",
        "schema retry 诊断没有区分 parse/schema error",
    )


def check_supervisor_schema_retry(root: Path) -> None:
    task_id = "task_supervisor_schema_retry"
    store = ArtifactStore(root / "supervisor_retry")
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    invalid = {
        "item": {
            "action": "CREATE_RESEARCH_UNIT",
            "target_need": "need_unknown",
            "research_goal": "继续研究。",
            "reason": "仍有缺口。",
        },
        "generated_by": "orchestrator",
        "output_language": "zh-CN",
    }
    valid = {
        "item": {
            "action": "FINISH",
            "reason": "预算已达到上限。",
        },
        "generated_by": "orchestrator",
        "output_language": "zh-CN",
    }
    provider = SequenceStructuredProvider([invalid, valid])
    raw, call, _output = LLMClient(
        config=config(), store=store, provider=provider
    ).generate_structured(
        task_id=task_id,
        agent_role=AgentRole.ORCHESTRATOR,
        agent_run_id="run_supervisor_retry",
        node_id="mission_supervisor_schema_retry",
        context_bundle=None,
        output_schema="ResearchMissionDecision",
        prompt_id="offline_supervisor",
        prompt_summary="决定 Mission 下一步。",
        artifacts={
            "research_mission": [{"id": "mission_retry"}],
            "mission_information_needs": [{"id": "need_real"}],
        },
    )
    require(
        raw["item"]["action"] == "FINISH"
        and len(provider.calls) == 2
        and call.metadata["structured_retry_result"] == "succeeded",
        "Supervisor schema 未复用统一 structured retry",
    )


def check_retry_failure_diagnostics(root: Path) -> None:
    task_id = "task_structured_retry_failure"
    store = ArtifactStore(root / "retry_failure")
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    provider = SequenceStructuredProvider(
        ['{"item":,}', '```json\n{"item":,}\n```']
    )
    try:
        generate_action(
            store=store,
            task_id=task_id,
            provider=provider,
            node_id="research_agent_action_retry_failure",
        )
    except LLMStructuredOutputError as exc:
        require(
            "stage=research_agent_action_retry_failure" in str(exc),
            "最终错误缺少 stage",
        )
    else:
        raise AssertionError("两次非法 JSON 被伪装为成功")
    require(len(provider.calls) == 2, "失败后发生超过一次 structured retry")
    call = store.load_many(task_id, "llm_calls")[-1]
    output = store.load_many(task_id, "llm_outputs")[-1]
    require(
        call["model"] == "offline-structured-model"
        and call["metadata"]["structured_retry_performed"] is True
        and call["metadata"]["structured_retry_result"] == "failed"
        and call["metadata"]["structured_parse_error"]
        and call["metadata"]["structured_retry_parse_error"]
        and call["metadata"]["structured_retry_response_chars"] > 0,
        "最终失败 trace 缺少 model/chars/parse/retry diagnostics",
    )
    require(
        output["validation_status"] == "failed"
        and output["metadata"]["retry_diagnostics"]["parse_error"],
        "最终失败 LLMOutput 未记录 retry 结果",
    )


def check_http_error_not_retried(root: Path) -> None:
    task_id = "task_structured_http_error"
    store = ArtifactStore(root / "http_error")
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    provider = SequenceStructuredProvider(
        [LLMProviderResponseError("模型接口 HTTP 402，balance insufficient")]
    )
    try:
        generate_action(
            store=store,
            task_id=task_id,
            provider=provider,
            node_id="research_agent_action_http_402",
        )
    except ValueError:
        require(len(provider.calls) == 1, "HTTP 402 被错误纳入 structured retry")
    else:
        raise AssertionError("HTTP 402 被 structured reliability 层吞掉")


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "kp_r2_llm_structured_output_reliability"
    )
    check_parser_variants()
    check_parse_retry(root)
    check_schema_retry(root)
    check_supervisor_schema_retry(root)
    check_retry_failure_diagnostics(root)
    check_http_error_not_retried(root)
    print("check_kp_r2_llm_structured_output_reliability: PASS")
    print("pure_json=true")
    print("markdown_json=true")
    print("surrounding_text_json=true")
    print("parse_retry_once=true")
    print("schema_retry_once=true")
    print("supervisor_shared_retry=true")
    print("failure_diagnostics=true")
    print("http_402_retry=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
