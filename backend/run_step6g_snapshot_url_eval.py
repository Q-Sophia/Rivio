from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Keep the same package initialization order as the existing Step6E checks.
from app.collection import CollectorQueueService  # noqa: F401
from app.agents.web_evidence import extract_evidence_from_page
from app.schemas import ResearchTask, SourceDocument, SourceEvidence, WebPageContent
from app.tools.web_collector import WebCollectorTool


TASK_ID = "task_step6g_snapshot_url_eval"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "app" / "data" / "snapshots" / "online_education"
OUTPUT_DIR = Path(__file__).resolve().parent / "app" / "data" / "evaluations" / "step6g_snapshot_url_eval"
DIMENSIONS = ["产品定位", "产品能力", "定价与成本", "生态与集成", "目标客户", "风险与限制"]


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def extract_for_intent(
    *,
    pages: list[WebPageContent],
    sources: list[SourceDocument],
    intent_key: str,
    objective: str,
    query_hints: list[str],
) -> list[SourceEvidence]:
    source_by_id = {item.id: item for item in sources}
    output: list[SourceEvidence] = []
    for page in pages:
        source = source_by_id[page.source_id]
        research_task = ResearchTask(
            id=f"research_{intent_key}_{source.id}",
            task_id=TASK_ID,
            information_need_id=f"need_{intent_key}",
            title=f"验证 {source.competitor} 的意图相关证据",
            objective=objective,
            competitor=source.competitor,
            dimension="产品能力",
            query_hints=query_hints,
            seed_urls=[source.url],
            snapshot_source_ids=[source.id],
            status="collected",
            stop_condition="输出最多 8 条与当前意图最相关、可逐字回放的证据。",
        )
        output.extend(
            extract_evidence_from_page(
                task_id=TASK_ID,
                research_task=research_task,
                source=source,
                page=page,
                max_items=8,
            )
        )
    return output


def main() -> None:
    source_rows = json.loads((SNAPSHOT_DIR / "sources.json").read_text(encoding="utf-8"))
    manual_rows = json.loads((SNAPSHOT_DIR / "evidence.json").read_text(encoding="utf-8"))
    sources = [SourceDocument(**{**item, "task_id": TASK_ID}) for item in source_rows]
    manual_evidence = [SourceEvidence(**{**item, "task_id": TASK_ID}) for item in manual_rows]

    reuse_pages = os.environ.get("STEP6G_REUSE_PAGES", "").strip() == "1"
    cached_pages_path = OUTPUT_DIR / "web_pages.json"
    cached_summary_path = OUTPUT_DIR / "evaluation_summary.json"
    if reuse_pages and cached_pages_path.is_file():
        pages = [
            WebPageContent(**item)
            for item in json.loads(cached_pages_path.read_text(encoding="utf-8"))
        ]
        cached_summary = (
            json.loads(cached_summary_path.read_text(encoding="utf-8"))
            if cached_summary_path.is_file()
            else {}
        )
        fetch_failures = list(cached_summary.get("fetch_failures") or [])
    else:
        pages = []
        fetch_failures = []
        tool = WebCollectorTool(timeout_seconds=25.0, respect_robots=True)
        try:
            for source in sources:
                try:
                    fetched = tool.fetch(source.url)
                    pages.append(
                        WebPageContent(
                            task_id=TASK_ID,
                            source_id=source.id,
                            requested_url=fetched.requested_url,
                            final_url=fetched.final_url,
                            title=fetched.title or source.title,
                            text=fetched.text,
                            content_type=fetched.content_type,
                            content_hash=fetched.content_hash,
                            render_mode=fetched.render_mode,
                            browser_engine=fetched.browser_engine,
                        )
                    )
                except Exception as exc:  # audit failure boundary; never fabricate page text
                    fetch_failures.append(
                        {
                            "source_id": source.id,
                            "url": source.url,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        finally:
            tool.close()

    source_by_id = {item.id: item for item in sources}
    page_by_source = {item.source_id: item for item in pages}
    extracted: list[SourceEvidence] = []
    for source_id, page in page_by_source.items():
        source = source_by_id[source_id]
        for dimension in DIMENSIONS:
            task = ResearchTask(
                id=f"research_{source_id}_{compact(dimension)}",
                task_id=TASK_ID,
                information_need_id=f"need_{compact(dimension)}",
                title=f"从固定网页核实 {source.competitor} 的{dimension}",
                objective=(
                    f"围绕竞品分析决策，核实 {source.competitor} 在{dimension}方面的"
                    "能力、适用范围、限制、成本与采用条件。"
                ),
                competitor=source.competitor,
                dimension=dimension,
                query_hints=[
                    f"{source.competitor} {dimension}",
                    "能力 价格 客户 场景 集成 限制",
                ],
                seed_urls=[source.url],
                snapshot_source_ids=[source.id],
                status="collected",
                stop_condition="固定网页完成证据抽取并与人工证据对照。",
            )
            extracted.extend(
                extract_evidence_from_page(
                    task_id=TASK_ID,
                    research_task=task,
                    source=source,
                    page=page,
                    max_items=12,
                )
            )

    unique_extracted: list[SourceEvidence] = []
    seen: set[tuple[str, str]] = set()
    for item in extracted:
        key = (item.source_id, compact(item.snippet))
        if key not in seen:
            seen.add(key)
            unique_extracted.append(item)

    recoverable_ids: list[str] = []
    recovered_ids: list[str] = []
    missing_recoverable_ids: list[str] = []
    unavailable_ids: list[str] = []
    extracted_by_source: dict[str, list[str]] = {}
    for item in unique_extracted:
        extracted_by_source.setdefault(item.source_id, []).append(compact(item.snippet))
    for item in manual_evidence:
        page = page_by_source.get(item.source_id)
        if page is None or compact(item.snippet) not in compact(page.text):
            unavailable_ids.append(item.id)
            continue
        recoverable_ids.append(item.id)
        target = compact(item.snippet)
        matched = any(
            target in candidate or candidate in target
            for candidate in extracted_by_source.get(item.source_id, [])
        )
        if matched:
            recovered_ids.append(item.id)
        else:
            missing_recoverable_ids.append(item.id)

    matched_extracted_keys: set[tuple[str, str]] = set()
    for manual in manual_evidence:
        target = compact(manual.snippet)
        for candidate in unique_extracted:
            value = compact(candidate.snippet)
            if manual.source_id == candidate.source_id and (target in value or value in target):
                matched_extracted_keys.add((candidate.source_id, value))
    additional = [
        item
        for item in unique_extracted
        if (item.source_id, compact(item.snippet)) not in matched_extracted_keys
    ]

    intent_a_terms = ("互动", "白板", "录制", "字幕", "课堂", "音视频", "课件")
    intent_b_terms = ("API", "SDK", "集成", "自托管", "部署", "运维", "兼容", "插件")
    intent_a = extract_for_intent(
        pages=pages,
        sources=sources,
        intent_key="teaching_experience",
        objective="核实实时音视频互动、互动白板、课堂录制、字幕和课件协作能力。",
        query_hints=list(intent_a_terms),
    )
    intent_b = extract_for_intent(
        pages=pages,
        sources=sources,
        intent_key="integration_deployment",
        objective="核实 API、SDK、LMS 集成、自托管、部署兼容性与运维责任。",
        query_hints=list(intent_b_terms),
    )
    intent_a_keys = {(item.source_id, compact(item.snippet)) for item in intent_a}
    intent_b_keys = {(item.source_id, compact(item.snippet)) for item in intent_b}
    intersection = intent_a_keys & intent_b_keys
    union = intent_a_keys | intent_b_keys

    def keyword_precision(items: list[SourceEvidence], terms: tuple[str, ...]) -> float:
        if not items:
            return 0.0
        relevant = sum(
            1
            for item in items
            if any(term.casefold() in item.snippet.casefold() for term in terms)
        )
        return relevant / len(items)

    recall = len(recovered_ids) / len(recoverable_ids) if recoverable_ids else 0.0
    summary = {
        "task_id": TASK_ID,
        "input_source_count": len(sources),
        "fetched_page_count": len(pages),
        "fetch_failure_count": len(fetch_failures),
        "manual_evidence_count": len(manual_evidence),
        "recoverable_manual_evidence_count": len(recoverable_ids),
        "recovered_manual_evidence_count": len(recovered_ids),
        "recoverable_manual_recall": round(recall, 4),
        "missing_recoverable_evidence_ids": missing_recoverable_ids,
        "currently_unavailable_manual_evidence_ids": unavailable_ids,
        "unique_extracted_evidence_count": len(unique_extracted),
        "additional_candidate_evidence_count": len(additional),
        "intent_a_evidence_count": len(intent_a_keys),
        "intent_b_evidence_count": len(intent_b_keys),
        "intent_evidence_overlap_count": len(intersection),
        "intent_evidence_jaccard": round(len(intersection) / len(union), 4) if union else 0.0,
        "intent_a_keyword_precision": round(keyword_precision(intent_a, intent_a_terms), 4),
        "intent_b_keyword_precision": round(keyword_precision(intent_b, intent_b_terms), 4),
        "fetch_failures": fetch_failures,
        "real_llm_calls": 0,
        "search_api_calls": 0,
        "manual_evidence_used_as_input": False,
        "reused_cached_pages": reuse_pages,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "sources.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in sources], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "web_pages.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in pages], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "extracted_evidence.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in unique_extracted], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "additional_candidate_evidence.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in additional], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "intent_a_evidence.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in intent_a], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "intent_b_evidence.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in intent_b], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
