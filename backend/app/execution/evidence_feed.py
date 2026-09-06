from __future__ import annotations

from typing import Any

from app.harness.artifacts import ArtifactStore
from app.tools.url_identity import canonical_source_url


def build_evidence_feed(
    store: ArtifactStore,
    task_id: str,
) -> list[dict[str, Any]]:
    """Project existing research artifacts into one source-oriented feed."""

    entries = _project_entries(store, task_id)
    return [
        _public_item(entry, status=entry["status"])
        for entry in entries
    ]


def build_evidence_feed_transitions(
    store: ArtifactStore,
    task_id: str,
) -> list[dict[str, Any]]:
    """Return every materialized status for SSE event synchronization."""

    transitions: list[dict[str, Any]] = []
    for entry in _project_entries(store, task_id):
        if entry["candidate"] is not None:
            transitions.append(_public_item(entry, status="discovered"))
        if entry.get("collection_failure") is not None:
            transitions.append(_public_item(entry, status="collection_failed"))
        if entry["source"] is not None:
            transitions.append(_public_item(entry, status="collected"))
        if entry["status"] == "verified":
            transitions.append(_public_item(entry, status="verified"))
    return transitions


def _project_entries(
    store: ArtifactStore,
    task_id: str,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in store.load_many(task_id, "research_source_candidates")
        if item.get("selected_for_collection") and str(item.get("url") or "").strip()
    ]
    sources = [
        item
        for item in store.load_many(task_id, "sources")
        if str(item.get("url") or "").strip()
    ]
    verified_source_ids = {
        str(item.get("source_id") or "")
        for item in store.load_many(task_id, "evidence")
        if str(item.get("source_id") or "")
    }
    collection_failures = _latest_collection_failures(store, task_id)

    entries: list[dict[str, Any]] = []
    entry_by_url: dict[str, dict[str, Any]] = {}
    entry_by_source_id: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        normalized_url = _normalized_url(url)
        if normalized_url in entry_by_url:
            continue
        entry = {
            "candidate": candidate,
            "source": None,
            "status": "discovered",
            "collection_failure": collection_failures.get(normalized_url),
        }
        if entry["collection_failure"] is not None:
            entry["status"] = "collection_failed"
        entries.append(entry)
        entry_by_url[normalized_url] = entry

    for source in sources:
        url = str(source.get("url") or "").strip()
        metadata = dict(source.get("metadata") or {})
        discovered_url = str(metadata.get("discovered_url") or "").strip()
        entry = (
            entry_by_url.get(_normalized_url(discovered_url))
            if discovered_url
            else None
        ) or entry_by_url.get(_normalized_url(url))

        if entry is None:
            entry = {
                "candidate": None,
                "source": source,
                "status": "collected",
                "collection_failure": None,
            }
            entries.append(entry)
        else:
            entry["source"] = source
            entry["status"] = "collected"

        entry_by_url[_normalized_url(url)] = entry
        if discovered_url:
            entry_by_url[_normalized_url(discovered_url)] = entry

        source_id = str(source.get("id") or "")
        if source_id:
            entry_by_source_id[source_id] = entry

    for source_id in verified_source_ids:
        entry = entry_by_source_id.get(source_id)
        if entry is not None:
            entry["status"] = "verified"

    return entries


def _public_item(
    entry: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    candidate = entry.get("candidate") or {}
    source = entry.get("source") or {}
    candidate_metadata = dict(candidate.get("metadata") or {})
    source_metadata = dict(source.get("metadata") or {})
    collection_failure = dict(entry.get("collection_failure") or {})

    source_url = str(source.get("url") or "").strip()
    candidate_url = str(candidate.get("url") or "").strip()
    url = source_url if status in {"collected", "verified"} and source_url else candidate_url
    title = str(source.get("title") or candidate.get("title") or url).strip()

    created_at = str(
        candidate.get("created_at")
        or candidate_metadata.get("created_at")
        or source.get("accessed_at")
        or ""
    )
    reliability_score = float(source.get("reliability_score") or 0)
    candidate_quality_score = _optional_float(candidate_metadata.get("final_score"))

    return {
        "id": str(candidate.get("id") or source.get("id") or url),
        "title": title,
        "url": url,
        "source_tool": _source_tool(
            candidate=candidate,
            candidate_metadata=candidate_metadata,
            source_metadata=source_metadata,
        ),
        "source_type": str(
            source.get("source_type")
            or candidate.get("source_type")
            or ""
        ),
        "status": status,
        "reliability_score": reliability_score,
        "candidate_quality_score": candidate_quality_score,
        "failure_reason": (
            _public_failure_reason(str(collection_failure.get("error") or ""))
            if status == "collection_failed"
            else ""
        ),
        "created_at": created_at,
    }


def _latest_collection_failures(
    store: ArtifactStore,
    task_id: str,
) -> dict[str, dict[str, Any]]:
    """Resolve the latest FETCH outcome per URL without creating a new artifact."""

    outcomes: dict[str, dict[str, Any]] = {}
    sequence = 0

    def record(*, url: str, status: str, error: str, created_at: Any) -> None:
        nonlocal sequence
        normalized_url = _normalized_url(url)
        if not normalized_url:
            return
        sequence += 1
        item = {
            "status": status.casefold(),
            "error": error,
            "created_at": str(created_at or ""),
            "sequence": sequence,
        }
        previous = outcomes.get(normalized_url)
        item_key = (item["created_at"], item["sequence"])
        previous_key = (
            str(previous.get("created_at") or ""),
            int(previous.get("sequence") or 0),
        ) if previous else ("", 0)
        if previous is None or item_key >= previous_key:
            outcomes[normalized_url] = item

    for attempt in store.load_many(task_id, "collection_attempts"):
        status = str(attempt.get("status") or "")
        for url in {
            str(attempt.get("requested_url") or "").strip(),
            str(attempt.get("final_url") or "").strip(),
        }:
            if url:
                record(
                    url=url,
                    status=status,
                    error=str(attempt.get("error") or ""),
                    created_at=attempt.get("created_at"),
                )

    for observation in store.load_many(task_id, "research_agent_observations"):
        if str(observation.get("action") or "").casefold() != "fetch":
            continue
        status = str(observation.get("status") or "")
        if status.casefold() not in {"completed", "failed"}:
            continue
        payload = dict(observation.get("payload") or {})
        url = str(payload.get("failed_url") or payload.get("url") or "").strip()
        if not url:
            continue
        record(
            url=url,
            status=status,
            error=str(payload.get("error") or observation.get("summary") or ""),
            created_at=observation.get("created_at"),
        )

    return {
        url: outcome
        for url, outcome in outcomes.items()
        if outcome.get("status") == "failed"
    }


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public_failure_reason(error: str) -> str:
    """Expose a concise operator-safe reason instead of a raw traceback."""

    lowered = error.casefold()
    if "robots" in lowered:
        return "站点 robots.txt 不允许自动采集"
    if "timeout" in lowered or "timed out" in lowered:
        return "采集请求超时"
    if "source quality" in lowered or "未通过 search/source quality" in lowered:
        return "候选来源关联或质量授权检查失败"
    if "没有可验证正文" in error or "正文过短" in error:
        return "未取得可验证的文章正文"
    if "unsafe" in lowered or "url 安全" in lowered or "non-public" in lowered:
        return "URL 安全检查未通过"
    if "nameerror" in lowered or "validationerror" in lowered:
        return "采集程序执行异常"
    cleaned = " ".join(error.split())
    return cleaned[:160] if cleaned else "采集未成功，等待重试"


def _source_tool(
    *,
    candidate: dict[str, Any],
    candidate_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> str:
    values = " ".join(
        str(value or "").casefold()
        for value in (
            candidate.get("source_tool"),
            candidate.get("provider"),
            candidate_metadata.get("provider"),
            source_metadata.get("source_tool"),
            source_metadata.get("provider"),
            source_metadata.get("collection_method"),
        )
    )
    if "zhihu" in values:
        return "zhihu_search"
    if "tavily" in values or "web_search" in values:
        return "tavily"
    return "other"


def _normalized_url(value: str) -> str:
    return canonical_source_url(value)
