from __future__ import annotations

import json
import os

import httpx

from app.llm.config import LLMConfig
from app.llm.provider import OpenAIChatCompletionsProvider, OpenAIResponsesProvider
from app.llm.structured import normalize_report_claim_references
from app.schemas import AgentRole, LLMMode, LLMProvider


def build_config(*, max_retries: int = 2) -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model="contract-test-model",
        mode=LLMMode.LLM,
        base_url="https://provider.test/v1",
        api_key_env="STEP6B_TEST_API_KEY",
        timeout_seconds=3,
        max_tokens=800,
        output_language="zh-CN",
        max_retries=max_retries,
        retry_base_seconds=0,
        enable_real_calls=True,
        api_style="responses",
    )


def validate_success_contract(errors: list[str]) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        output = {
            "items": [
                {
                    "id": "cl_contract",
                    "task_id": "task_contract",
                    "dimension": "feature",
                    "claim_text": "中文契约测试结论。",
                    "competitors": ["产品A"],
                    "evidence_ids": ["ev_contract"],
                    "confidence": 0.8,
                    "produced_by_agent_run_id": "run_contract",
                    "citation_status": "pending",
                }
            ],
            "generated_by": "analyst",
            "output_language": "zh-CN",
        }
        payload = {
            "id": "resp_contract_success",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(output, ensure_ascii=False)}
                    ],
                }
            ],
            "usage": {"input_tokens": 120, "output_tokens": 80},
        }
        return httpx.Response(
            200,
            json=payload,
            headers={"x-request-id": "req_contract_success"},
        )

    provider = OpenAIResponsesProvider(
        config=build_config(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    result = provider.generate(
        task_id="task_contract",
        agent_role=AgentRole.ANALYST,
        output_schema="AnalysisClaim[]",
        prompt_summary="生成简体中文 AnalysisClaim[]（分析结论）。",
        system_context=["只能使用输入证据。"],
        artifacts={
            "evidence": [
                {
                    "id": "ev_contract",
                    "task_id": "task_contract",
                    "source_id": "src_contract",
                    "competitor": "产品A",
                    "dimension": "feature",
                    "snippet": "产品A提供互动能力。",
                    "normalized_fact": "产品A提供互动能力。",
                    "confidence": 0.8,
                }
            ]
        },
    )
    if len(calls) != 1:
        errors.append(f"success contract expected 1 request, found {len(calls)}")
        return
    request = calls[0]
    if request.url != httpx.URL("https://provider.test/v1/responses"):
        errors.append(f"unexpected responses url={request.url}")
    if request.headers.get("authorization") != "Bearer contract-secret":
        errors.append("Authorization header mismatch")
    request_payload = json.loads(request.content)
    text_format = request_payload.get("text", {}).get("format", {})
    if text_format.get("type") != "json_schema" or text_format.get("strict") is not True:
        errors.append("request missing strict JSON Schema format")
    if request_payload.get("store") is not False:
        errors.append("request store must be false")
    if result.raw_output.get("output_language") != "zh-CN":
        errors.append("structured output language mismatch")
    if result.request_id != "resp_contract_success":
        errors.append(f"request id mismatch={result.request_id}")
    if result.input_tokens != 120 or result.output_tokens != 80:
        errors.append("usage token extraction mismatch")


def validate_retry_contract(errors: list[str]) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                429,
                json={"error": {"message": "rate limited"}},
                headers={"x-request-id": f"req_retry_{attempts}"},
            )
        output = {
            "item": {
                "id": "report_contract",
                "task_id": "task_contract",
                "title": "中文测试报告",
                "markdown": "# 中文测试报告\n[cl_contract]",
                "claim_ids": ["cl_contract"],
                "created_by_agent_run_id": "run_contract",
            },
            "generated_by": "writer",
            "output_language": "zh-CN",
        }
        return httpx.Response(
            200,
            json={
                "id": "resp_retry_success",
                "status": "completed",
                "output_text": json.dumps(output, ensure_ascii=False),
            },
        )

    provider = OpenAIResponsesProvider(
        config=build_config(max_retries=2),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    result = provider.generate(
        task_id="task_contract",
        agent_role=AgentRole.WRITER,
        output_schema="CompetitiveReport",
        prompt_summary="生成简体中文报告。",
        system_context=[],
        artifacts={"claims": [{"id": "cl_contract", "claim_text": "中文结论"}]},
    )
    if attempts != 3 or result.attempts != 3:
        errors.append(
            f"retry contract expected 3 attempts, handler={attempts}, result={result.attempts}"
        )


def validate_chat_completions_contract(errors: list[str]) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        output = {
            "items": [
                {
                    "id": "cl_domestic",
                    "task_id": "task_contract",
                    "dimension": "feature",
                    "claim_text": "国内兼容接口中文测试结论。",
                    "competitors": ["产品A"],
                    "evidence_ids": ["ev_contract"],
                    "confidence": 0.8,
                    "produced_by_agent_run_id": "run_contract",
                    "citation_status": "pending",
                }
            ],
            "generated_by": "analyst",
            "output_language": "zh-CN",
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_contract",
                "choices": [
                    {"message": {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            },
        )

    config = LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model="domestic-contract-model",
        mode=LLMMode.LLM,
        base_url="https://domestic-provider.test/v1",
        api_key_env="STEP6B_TEST_API_KEY",
        enable_real_calls=True,
        api_style="chat_completions",
        structured_output_mode="json_schema",
        thinking_mode="disabled",
    )
    provider = OpenAIChatCompletionsProvider(
        config=config,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    result = provider.generate(
        task_id="task_contract",
        agent_role=AgentRole.ANALYST,
        output_schema="AnalysisClaim[]",
        prompt_summary="生成简体中文结论。",
        system_context=[],
        artifacts={"evidence": [{"id": "ev_contract"}]},
    )
    if len(calls) != 1:
        errors.append(f"chat contract expected 1 request, found {len(calls)}")
        return
    if calls[0].url != httpx.URL("https://domestic-provider.test/v1/chat/completions"):
        errors.append(f"unexpected chat completions url={calls[0].url}")
    payload = json.loads(calls[0].content)
    if "messages" not in payload or payload.get("stream") is not False:
        errors.append("chat completions payload mismatch")
    response_format = payload.get("response_format", {})
    if response_format.get("type") != "json_schema":
        errors.append("chat completions request missing json_schema response_format")
    if payload.get("thinking") != {"type": "disabled"}:
        errors.append("chat completions request missing disabled thinking mode")
    if result.input_tokens != 100 or result.output_tokens != 60:
        errors.append("chat completions token usage mismatch")

    fenced = OpenAIChatCompletionsProvider.extract_structured_output(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "```json\n{\"result\": \"中文\"}\n```"
                    },
                }
            ]
        }
    )
    if fenced != {"result": "中文"}:
        errors.append("chat completions fenced JSON normalization failed")


def validate_safety_gate(errors: list[str]) -> None:
    config = LLMConfig(
        provider=LLMProvider.OPENAI,
        model="contract-test-model",
        mode=LLMMode.LLM_WITH_FALLBACK,
        base_url="https://api.openai.com/v1",
        api_key_env="STEP6B_TEST_API_KEY",
        enable_real_calls=False,
        api_style="responses",
    )
    readiness_errors = config.real_call_readiness_errors()
    if not any("LLM_ENABLE_REAL_CALLS=false" in error for error in readiness_errors):
        errors.append("real-call safety gate did not block disabled configuration")


def validate_report_claim_normalization(errors: list[str]) -> None:
    raw_output = {
        "item": {
            "id": "report_contract",
            "task_id": "task_contract",
            "title": "中文测试报告",
            "markdown": "# 中文测试报告\n\n已有引用 [claim_001]。",
            "claim_ids": ["claim_001"],
            "created_by_agent_run_id": "run_contract",
        },
        "generated_by": "writer",
        "output_language": "zh-CN",
    }
    normalized, added = normalize_report_claim_references(
        raw_output,
        ["claim_001", "claim_002"],
    )
    item = normalized.get("item", {})
    if item.get("claim_ids") != ["claim_001", "claim_002"]:
        errors.append("report claim normalization did not preserve known claim ids")
    if added != ["claim_002"] or "[claim_002]" not in item.get("markdown", ""):
        errors.append("report claim normalization did not append missing markdown ref")

    unknown_output = {
        **raw_output,
        "item": {
            **raw_output["item"],
            "claim_ids": ["claim_unknown"],
        },
    }
    unknown_normalized, unknown_added = normalize_report_claim_references(
        unknown_output,
        ["claim_001"],
    )
    if "claim_unknown" not in unknown_normalized["item"]["claim_ids"]:
        errors.append("report normalization must preserve unknown ids for later rejection")
    if "claim_unknown" in unknown_added:
        errors.append("report normalization must not add unknown claim refs")


def main() -> None:
    previous = os.environ.get("STEP6B_TEST_API_KEY")
    os.environ["STEP6B_TEST_API_KEY"] = "contract-secret"
    errors: list[str] = []
    try:
        validate_success_contract(errors)
        validate_retry_contract(errors)
        validate_chat_completions_contract(errors)
        validate_safety_gate(errors)
        validate_report_claim_normalization(errors)
    finally:
        if previous is None:
            os.environ.pop("STEP6B_TEST_API_KEY", None)
        else:
            os.environ["STEP6B_TEST_API_KEY"] = previous

    if errors:
        print("FAIL")
        print(f"errors={len(errors)}")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        raise SystemExit(1)
    print("PASS")
    print("errors=0")
    print("network_used=False")
    print("success_contract=True")
    print("retry_contract=True")
    print("chat_completions_contract=True")
    print("real_call_safety_gate=True")
    print("report_claim_normalization=True")


if __name__ == "__main__":
    main()
