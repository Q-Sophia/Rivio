from __future__ import annotations

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
