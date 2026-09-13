from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.harness.artifacts import ArtifactStore


_COVERED = {"sufficient", "not_applicable"}
_SUPPLEMENT_SOURCE = "r1_bounded_coverage_supplement"


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _token_totals(calls: list[dict[str, Any]]) -> tuple[int | None, int | None, int | None]:
    if not calls:
        return 0, 0, 0
    input_values: list[int] = []
    output_values: list[int] = []
    for call in calls:
        metadata = call.get("metadata", {})
        if "input_tokens" not in metadata or "output_tokens" not in metadata:
            return None, None, None
        input_values.append(int(metadata["input_tokens"]))
        output_values.append(int(metadata["output_tokens"]))
    input_tokens = sum(input_values)
    output_tokens = sum(output_values)
    return input_tokens, output_tokens, input_tokens + output_tokens


def _need_coverage(store: ArtifactStore, task_id: str) -> tuple[int, int, float | None]:
    needs = store.load_many(task_id, "research_information_needs")
    coverage = store.load_many(task_id, "evidence_coverage")
    covered = 0
    for need in needs:
        dimension = str(need.get("dimension") or "")
        matching = [
            item
            for item in coverage
            if str(item.get("dimension") or "") == dimension
        ]
        if matching and all(
            str(item.get("status") or "") in _COVERED for item in matching
        ):
            covered += 1
    total = len(needs)
    return covered, total, covered / total if total else None


def _valid_evidence_count(evidence: list[dict[str, Any]]) -> int | None:
    if not evidence:
        return 0
    values = [item.get("metadata", {}).get("quote_verified") for item in evidence]
    if any(value is None for value in values):
        return None
    return sum(value is True for value in values)


def _official_source_metrics(
    sources: list[dict[str, Any]],
) -> tuple[int | None, float | None]:
    if not sources:
        return 0, 0.0
    explicit = []
    for source in sources:
        metadata = source.get("metadata", {})
        confidence = metadata.get("official_confidence")
        first_party = metadata.get("first_party")
        if confidence is None or first_party is None:
            return None, None
        explicit.append(confidence == "confirmed" and first_party is True)
    count = sum(explicit)
    return count, count / len(sources)


def collect_e2_metrics(
    *,
    store: ArtifactStore,
    task_id: str,
    case_id: str,
    variant: str,
    elapsed_ms: int,
    execution_error: str = "",
) -> dict[str, Any]:
    evidence = store.load_many(task_id, "evidence")
    sources = store.load_many(task_id, "sources")
    tool_calls = store.load_many(task_id, "tool_calls")
    llm_calls = store.load_many(task_id, "llm_calls")
    covered, total, coverage_ratio = _need_coverage(store, task_id)
    official_count, official_ratio = _official_source_metrics(sources)
    input_tokens, output_tokens, total_tokens = _token_totals(llm_calls)
    actions = store.load_many(task_id, "research_agent_actions")
    if variant == "legacy":
        search_count: int | None = len(store.load_many(task_id, "search_attempts"))
        fetch_count: int | None = len(
            store.load_many(task_id, "collection_attempts")
        )
        read_count: int | None = None
        terminal = store.load_many(task_id, "research_loop_runs")
        success = bool(terminal and terminal[-1].get("status") == "completed")
    else:
        action_values = [str(item.get("action") or "") for item in actions]
        search_count = action_values.count("SEARCH")
        fetch_count = action_values.count("FETCH")
        read_count = action_values.count("READ")
        terminal = store.load_many(task_id, "research_agent_coordinator_runs")
        success = bool(
            terminal
            and terminal[-1].get("status") == "completed"
            and terminal[-1].get("result_status") != "FAILED"
        )
    return {
        "case": case_id,
        "variant": variant,
        "task_id": task_id,
        "research_success": success and not execution_error,
        "execution_error": execution_error,
        "evidence_count": len(evidence),
        "valid_evidence_count": _valid_evidence_count(evidence),
        "evidence_coverage": coverage_ratio,
        "coverage": coverage_ratio,
        "covered_need_count": covered,
        "total_need_count": total,
        "unique_source_count": len({item.get("url") for item in sources if item.get("url")}),
        "unique_sources": len({item.get("url") for item in sources if item.get("url")}),
        "official_source_count": official_count,
        "official_source_ratio": official_ratio,
        "search_count": search_count,
        "fetch_count": fetch_count,
        "read_count": read_count,
        "tool_call_count": len(tool_calls),
        "tool_calls": len(tool_calls),
        "llm_call_count": len(llm_calls),
        "llm_calls": len(llm_calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "tokens": total_tokens,
        "elapsed_ms": elapsed_ms,
    }


def _percentile(values: list[int], percentile: float) -> float | None:
    """Return a linearly interpolated percentile from recorded values."""

    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _context_section_metrics(
    traces: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    section_names = sorted(
        {
            str(section)
            for trace in traces
            for section in trace.get("section_chars", {})
        }
        | {
            str(section)
            for trace in traces
            for section in trace.get("section_estimated_tokens", {})
        }
    )
    metrics: dict[str, dict[str, Any]] = {}
    for section in section_names:
        char_values = [
            int(trace.get("section_chars", {})[section])
            for trace in traces
            if section in trace.get("section_chars", {})
        ]
        token_values = [
            int(trace.get("section_estimated_tokens", {})[section])
            for trace in traces
            if section in trace.get("section_estimated_tokens", {})
        ]
        metrics[section] = {
            "sample_count": len(char_values),
            "total_chars": sum(char_values),
            "avg_chars_per_action": (
                sum(char_values) / len(char_values)
                if char_values
                else None
            ),
            "p50_chars": _percentile(char_values, 0.50),
            "p95_chars": _percentile(char_values, 0.95),
            "max_chars": max(char_values) if char_values else None,
            "total_estimated_tokens": sum(token_values),
            "avg_estimated_tokens_per_action": (
                sum(token_values) / len(token_values)
                if token_values
                else None
            ),
        }
    return metrics


def _context_actual_input_tokens(
    *,
    traces: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]],
) -> tuple[list[int] | None, int]:
    """Return actual Research Action usage only when every trace has usage."""

    calls_by_node = {
        str(item.get("node_id") or ""): item
        for item in llm_calls
        if item.get("node_id")
    }
    values: list[int] = []
    missing_count = 0
    for trace in traces:
        raw_value = trace.get("input_tokens")
        trace_metadata = trace.get("metadata", {})
        explicitly_available = trace_metadata.get("usage_available")
        node_id = str(trace_metadata.get("node_id") or "")
        call = calls_by_node.get(node_id)
        if call is not None:
            call_metadata = call.get("metadata", {})
            call_input = call_metadata.get("input_tokens")
            provider_result_available = bool(
                int(call_metadata.get("attempts") or 0) > 0
                and call_input is not None
                and int(call_input or 0) > 0
            )
            if call_metadata.get("usage_available") is False:
                provider_result_available = False
            if explicitly_available is None:
                explicitly_available = provider_result_available
        available = bool(
            raw_value is not None
            and explicitly_available is not False
            and (
                call is None
                or provider_result_available
            )
        )
        if not available:
            missing_count += 1
            continue
        values.append(int(raw_value))
    if not traces or missing_count:
        return None, missing_count or (1 if not traces else 0)
    return values, 0


def _failed_llm_diagnostics(
    llm_calls: list[dict[str, Any]],
) -> tuple[int, int, list[str]]:
    failed = [
        item
        for item in llm_calls
        if str(item.get("status") or "").casefold() == "failed"
    ]
    provider_markers = (
        "provider",
        "http 4",
        "http 5",
        "insufficient balance",
        "timeout",
        "timed out",
        "transport",
        "connection",
        "network",
        "模型接口",
    )
    provider_failed = [
        item
        for item in failed
        if any(
            marker in str(item.get("error") or "").casefold()
            for marker in provider_markers
        )
    ]
    errors = sorted(
        {
            str(item.get("error") or "").strip()
            for item in failed
            if str(item.get("error") or "").strip()
        }
    )
    return len(failed), len(provider_failed), errors


def collect_context_governance_metrics(
    *,
    store: ArtifactStore,
    task_id: str,
    case_id: str,
    variant: str,
    context_governance_enabled: bool,
    elapsed_ms: int,
    execution_error: str = "",
) -> dict[str, Any]:
    """Collect Research Action-only cost and persisted quality metrics."""

    traces = store.load_many(task_id, "research_action_context_traces")
    actions = store.load_many(task_id, "research_agent_actions")
    llm_calls = store.load_many(task_id, "llm_calls")
    task_failures = store.load_many(task_id, "research_task_failures")
    evidence = store.load_many(task_id, "evidence")
    covered, total, coverage_ratio = _need_coverage(store, task_id)
    token_values, usage_missing_count = _context_actual_input_tokens(
        traces=traces,
        llm_calls=llm_calls,
    )
    latency_values = [
        int(item.get("llm_latency_ms") or 0) for item in traces
        if item.get("llm_latency_ms") is not None
    ]
    coordinator_runs = store.load_many(
        task_id,
        "research_agent_coordinator_runs",
    )
    final_run = coordinator_runs[-1] if coordinator_runs else {}
    gap_value = final_run.get("research_gap_count")
    final_gap_count = (
        int(gap_value)
        if gap_value is not None
        else len(store.load_many(task_id, "research_gaps"))
    )
    trace_modes = sorted(
        {
            str(item.get("context_mode") or "")
            for item in traces
            if item.get("context_mode")
        }
    )
    expected_trace_mode = (
        "governed" if context_governance_enabled else "legacy"
    )
    recorded_context_flag = final_run.get(
        "context_governance_enabled"
    )
    failed_llm_call_count, provider_failure_count, llm_errors = (
        _failed_llm_diagnostics(llm_calls)
    )
    task_failure_errors = sorted(
        {
            str(
                item.get("error_message")
                or item.get("error")
                or ""
            ).strip()
            for item in task_failures
            if str(
                item.get("error_message")
                or item.get("error")
                or ""
            ).strip()
        }
    )
    coordinator_status = str(final_run.get("status") or "")
    coordinator_result_status = str(
        final_run.get("result_status") or ""
    )
    coordinator_error = str(final_run.get("error") or "")
    stop_reason = str(final_run.get("stop_reason") or "")
    terminal_failure_reason = execution_error or coordinator_error
    research_success = bool(
        final_run
        and coordinator_status == "completed"
        and coordinator_result_status != "FAILED"
        and not terminal_failure_reason
    )
    return {
        "case": case_id,
        "variant": variant,
        "task_id": task_id,
        "context_governance_enabled": context_governance_enabled,
        "research_success": research_success,
        "execution_error": terminal_failure_reason,
        "failure_reason": terminal_failure_reason,
        "total_input_tokens": (
            sum(token_values) if token_values is not None else None
        ),
        "avg_input_tokens_per_action": (
            sum(token_values) / len(token_values)
            if token_values is not None and token_values
            else None
        ),
        "p50_input_tokens": (
            _percentile(token_values, 0.50)
            if token_values is not None
            else None
        ),
        "p95_input_tokens": (
            _percentile(token_values, 0.95)
            if token_values is not None
            else None
        ),
        "max_input_tokens": (
            max(token_values)
            if token_values is not None and token_values
            else None
        ),
        "actual_input_tokens_available": token_values is not None,
        "input_token_usage_missing_count": usage_missing_count,
        "llm_total_latency_ms": (
            sum(latency_values)
            if traces and len(latency_values) == len(traces)
            else None
        ),
        "total_elapsed_ms": elapsed_ms,
        "action_count": len(actions),
        "successful_action_count": len(actions),
        "context_trace_count": len(traces),
        "trace_action_count_match": len(traces) == len(actions),
        "failed_llm_call_count": failed_llm_call_count,
        "provider_failure_count": provider_failure_count,
        "llm_failure_errors": llm_errors,
        "research_task_failure_count": len(task_failures),
        "research_task_failure_errors": task_failure_errors,
        "evidence_count": len(evidence),
        "valid_evidence_count": _valid_evidence_count(evidence),
        "coverage": coverage_ratio,
        "covered_need_count": covered,
        "total_need_count": total,
        "final_gap_count": final_gap_count,
        "stop_reason": stop_reason,
        "coordinator_status": coordinator_status,
        "coordinator_result_status": coordinator_result_status,
        "coordinator_error": coordinator_error,
        "coordinator_failed_task_count": int(
            final_run.get("failed_tasks") or 0
        ),
        "coordinator_context_governance_enabled": (
            recorded_context_flag
        ),
        "trace_context_modes": trace_modes,
        "context_configuration_match": bool(
            recorded_context_flag is context_governance_enabled
            and trace_modes == [expected_trace_mode]
        ),
        "context_sections": _context_section_metrics(traces),
        "live_web_nondeterminism": True,
    }


def _coverage_ratio(summary: dict[str, Any]) -> float | None:
    counts = summary.get("coverage_status_counts", {})
    if not isinstance(counts, dict):
        return None
    total = sum(int(value) for value in counts.values())
    if not total:
        return None
    covered = sum(int(counts.get(status, 0)) for status in _COVERED)
    return covered / total


def _calls_before(
    values: list[dict[str, Any]],
    cutoff: datetime | None,
) -> list[dict[str, Any]]:
    if cutoff is None:
        return []
    return [
        item
        for item in values
        if (created := _time(item.get("created_at"))) is not None
        and created <= cutoff
    ]


def _supplement_tasks(store: ArtifactStore, task_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in store.load_many(task_id, "research_tasks")
        if item.get("metadata", {}).get("source") == _SUPPLEMENT_SOURCE
        or item.get("research_gap_id")
        or item.get("parent_research_task_id")
    ]


def validate_supplement_provenance(
    store: ArtifactStore,
    task_id: str,
) -> list[str]:
    all_tasks = store.load_many(task_id, "research_tasks")
    supplement_tasks = _supplement_tasks(store, task_id)
    need_ids = {
        item.get("id")
        for item in store.load_many(task_id, "research_information_needs")
    }
    task_ids = {item.get("id") for item in all_tasks}
    gaps = {item.get("id") for item in store.load_many(task_id, "research_gaps")}
    plans = store.load_many(task_id, "research_plans")
    max_rounds = int(
        plans[-1].get("budget", {}).get("max_collection_rounds", 3)
        if plans
        else 3
    )
    errors: list[str] = []
    semantic_keys: set[tuple[Any, ...]] = set()
    for task in supplement_tasks:
        task_ref = str(task.get("id") or "")
        gap_id = str(task.get("research_gap_id") or "")
        metadata_gap_ids = {
            str(item)
            for item in task.get("metadata", {}).get("trigger_gap_ids", [])
        }
        if not gap_id or (gap_id not in gaps and gap_id not in metadata_gap_ids):
            errors.append(f"{task_ref}: missing_gap_provenance")
        if task.get("information_need_id") not in need_ids:
            errors.append(f"{task_ref}: missing_information_need_provenance")
        parent = str(task.get("parent_research_task_id") or "")
        if not parent or parent not in task_ids:
            errors.append(f"{task_ref}: missing_parent_provenance")
        round_number = int(task.get("collection_round") or 0)
        if round_number <= 1 or round_number > max_rounds:
            errors.append(f"{task_ref}: collection_round_out_of_bounds")
        key = (
            round_number,
            str(task.get("competitor") or "").casefold(),
            str(task.get("dimension") or "").casefold(),
        )
        if key in semantic_keys:
            errors.append(f"{task_ref}: duplicate_supplement_scope")
        semantic_keys.add(key)
    coordinator_runs = store.load_many(
        task_id, "research_agent_coordinator_runs"
    )
    if coordinator_runs:
        final_run = coordinator_runs[-1]
        if int(final_run.get("current_collection_round") or 0) > int(
            final_run.get("max_collection_rounds") or max_rounds
        ):
            errors.append("coordinator_collection_round_budget_exceeded")
        if int(final_run.get("source_count") or 0) > int(
            final_run.get("max_total_sources") or 0
        ):
            errors.append("coordinator_source_budget_exceeded")
        if int(final_run.get("actions_completed") or 0) > int(
            final_run.get("max_actions") or 0
        ):
            errors.append("coordinator_action_budget_exceeded")
    return errors


def collect_e3_metrics(
    *,
    store: ArtifactStore,
    task_id: str,
    case_id: str,
    variant: str,
    elapsed_ms: int,
    execution_error: str = "",
) -> dict[str, Any]:
    events = store.load_many(task_id, "research_agent_coordinator_events")
    coverage_events = [
        item for item in events if item.get("event_type") == "coverage_refreshed"
    ]
    initial_event = coverage_events[0] if coverage_events else {}
    final_event = coverage_events[-1] if coverage_events else {}
    initial_summary = initial_event.get("data", {}).get("coverage_summary", {})
    final_summary = final_event.get("data", {}).get("coverage_summary", {})
    initial_coverage = _coverage_ratio(initial_summary)
    final_coverage = _coverage_ratio(final_summary)
    initial_gap_ids = (
        set(initial_summary.get("research_gap_ids", []))
        if initial_summary
        else None
    )
    final_gap_ids = (
        set(final_summary.get("research_gap_ids", []))
        if final_summary
        else None
    )
    initial_gap_count = (
        int(initial_summary.get("research_gap_count", 0) or 0)
        if initial_summary
        else None
    )
    final_gap_count = (
        int(final_summary.get("research_gap_count", 0) or 0)
        if final_summary
        else None
    )
    closed_gap_count = (
        len(initial_gap_ids - final_gap_ids)
        if initial_gap_ids is not None and final_gap_ids is not None
        else None
    )
    tool_calls = store.load_many(task_id, "tool_calls")
    llm_calls = store.load_many(task_id, "llm_calls")
    cutoff = _time(initial_event.get("created_at"))
    base_tools = _calls_before(tool_calls, cutoff) if cutoff else None
    base_llms = _calls_before(llm_calls, cutoff) if cutoff else None
    base_input, base_output, base_tokens = (
        _token_totals(base_llms)
        if base_llms is not None
        else (None, None, None)
    )
    final_input, final_output, final_tokens = _token_totals(llm_calls)
    coordinator_runs = store.load_many(task_id, "research_agent_coordinator_runs")
    final_run = coordinator_runs[-1] if coordinator_runs else {}
    started_at = _time(final_run.get("started_at"))
    completed_at = _time(final_run.get("completed_at"))
    final_elapsed = (
        int((completed_at - started_at).total_seconds() * 1000)
        if completed_at is not None and started_at is not None
        else elapsed_ms
    )
    base_elapsed = (
        int((cutoff - started_at).total_seconds() * 1000)
        if cutoff is not None and started_at is not None
        else None
    )
    supplement_tasks = _supplement_tasks(store, task_id)
    supplement_rounds = sorted(
        {
            int(item.get("collection_round") or 0)
            for item in supplement_tasks
            if int(item.get("collection_round") or 0) > 1
        }
    )
    provenance_errors = validate_supplement_provenance(store, task_id)
    initial_evidence_count = initial_summary.get("projection", {}).get(
        "verified_evidence_count"
    )
    if initial_evidence_count is not None:
        initial_evidence_count = int(initial_evidence_count)
    final_evidence_count = len(store.load_many(task_id, "evidence"))
    budget_stop_count = sum(
        1
        for item in events
        if "budget" in str(item.get("data", {}).get("stop_reason") or "")
    )
    return {
        "case": case_id,
        "variant": variant,
        "task_id": task_id,
        "research_success": bool(
            final_run
            and final_run.get("status") == "completed"
            and final_run.get("result_status") != "FAILED"
            and not execution_error
        ),
        "execution_error": execution_error,
        "initial_coverage": initial_coverage,
        "final_coverage": final_coverage,
        "coverage_delta": (
            final_coverage - initial_coverage
            if initial_coverage is not None and final_coverage is not None
            else None
        ),
        "initial_gap_count": initial_gap_count,
        "final_gap_count": final_gap_count,
        "closed_gap_count": closed_gap_count,
        "gap_closure_rate": (
            closed_gap_count / initial_gap_count
            if closed_gap_count is not None and initial_gap_count
            else None
        ),
        "initial_evidence_count": initial_evidence_count,
        "final_evidence_count": final_evidence_count,
        "supplement_task_count": len(supplement_tasks),
        "supplement_round_count": len(supplement_rounds),
        "supplement_rounds": len(supplement_rounds),
        "base_tool_calls": len(base_tools) if base_tools is not None else None,
        "final_tool_calls": len(tool_calls),
        "extra_tool_calls": (
            len(tool_calls) - len(base_tools)
            if base_tools is not None
            else None
        ),
        "base_llm_calls": len(base_llms) if base_llms is not None else None,
        "final_llm_calls": len(llm_calls),
        "extra_llm_calls": (
            len(llm_calls) - len(base_llms)
            if base_llms is not None
            else None
        ),
        "base_input_tokens": base_input,
        "base_output_tokens": base_output,
        "base_tokens": base_tokens,
        "final_input_tokens": final_input,
        "final_output_tokens": final_output,
        "final_tokens": final_tokens,
        "extra_tokens": (
            final_tokens - base_tokens
            if final_tokens is not None and base_tokens is not None
            else None
        ),
        "base_elapsed_ms": base_elapsed,
        "final_elapsed_ms": final_elapsed,
        "extra_elapsed_ms": (
            final_elapsed - base_elapsed if base_elapsed is not None else None
        ),
        "budget_stop_count": budget_stop_count,
        "supplement_provenance_valid": not provenance_errors,
        "supplement_provenance_errors": provenance_errors,
        "live_web_nondeterminism": True,
    }


def build_search_audit(store: ArtifactStore, task_id: str) -> list[dict[str, Any]]:
    attempts = store.load_many(task_id, "search_attempts")
    results = store.load_many(task_id, "web_search_results")
    tool_calls = store.load_many(task_id, "tool_calls")
    audit: list[dict[str, Any]] = []
    for attempt in attempts:
        matching_results = [
            item
            for item in results
            if item.get("search_attempt_id") == attempt.get("id")
        ]
        matching_call = next(
            (
                item
                for item in tool_calls
                if item.get("tool_name") == "web_search"
                and item.get("input", {}).get("query") == attempt.get("query")
                and item.get("input", {}).get("research_task_id")
                == attempt.get("research_task_id")
            ),
            None,
        )
        call_input = matching_call.get("input", {}) if matching_call else {}
        audit.append(
            {
                "search_attempt_id": attempt.get("id"),
                "research_task_id": attempt.get("research_task_id"),
                "query": attempt.get("query"),
                "provider": attempt.get("provider"),
                "timestamp": attempt.get("created_at"),
                "returned_urls": [
                    item.get("url") for item in matching_results if item.get("url")
                ],
                "transport": call_input.get("transport"),
                "status": attempt.get("status"),
                "error": attempt.get("error", ""),
            }
        )
    return audit
