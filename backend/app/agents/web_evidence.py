from __future__ import annotations

import hashlib
import re

from app.agents.base import BaseAgent
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentContext,
    AgentResult,
    AgentRole,
    EvidenceDimension,
    EvidenceExtractionAttempt,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    SourceEvidence,
    WebPageContent,
)
from build_product_cards_demo import validate_source_evidence_links


DIMENSION_KEYWORDS = {
    EvidenceDimension.POSITIONING.value: (
        "定位", "面向", "适用于", "场景", "解决方案", "客户端", "平台", "在线直播", "教学",
        "多人实时互动", "课件"
    ),
    EvidenceDimension.PRICING.value: ("价格", "定价", "费用", "收费", "免费", "套餐", "元"),
    EvidenceDimension.FEATURE.value: (
        "功能", "支持", "提供", "会议", "课堂", "共享", "录制", "字幕", "互动", "协作",
        "直播", "连麦", "延时", "音视频", "白板", "课件"
    ),
    EvidenceDimension.ECOSYSTEM.value: (
        "集成", "接口", "API", "SDK", "生态", "兼容", "插件", "LMS", "自托管", "部署", "运维"
    ),
    EvidenceDimension.CUSTOMER.value: ("客户", "用户", "企业", "学校", "教师", "学生", "机构"),
    EvidenceDimension.RISK.value: ("限制", "风险", "安全", "隐私", "合规", "上限", "自托管", "部署", "运维", "责任"),
}

LOW_VALUE_PATTERNS = (
    "订阅获取",
    "同意腾讯会议通过邮件",
    "最新资讯",
    "未来，",
    "持续更新",
    "带来更多",
    "敬请期待",
    "为您增添",
    "新功能到来",
)


def normalize_dimension(value: str) -> str:
    # Local import avoids the legacy app.agents/app.collection package-init
    # cycle while keeping one canonical mapping implementation.
    from app.collection.source_quality import canonical_dimension

    return canonical_dimension(value)


def evidence_span_identity(item: SourceEvidence) -> tuple[str, str, int, int]:
    return (
        item.source_id,
        str(item.dimension),
        item.source_text_start,
        item.source_text_end,
    )


def verify_candidate_evidence(
    *,
    task_id: str,
    research_task: ResearchTask,
    source: SourceDocument,
    page: WebPageContent,
    chunk: SourceChunk,
    exact_quote: str,
    supports: str,
) -> SourceEvidence:
    """Verify an agent-proposed quote against the current immutable page text."""

    if not exact_quote or not exact_quote.strip():
        raise ValueError("Candidate Evidence exact_quote 不能为空")
    if any(item.task_id != task_id for item in (source, page, chunk)):
        raise ValueError("Candidate Evidence 不允许跨 task")
    if research_task.task_id != task_id:
        raise ValueError("Candidate Evidence 的 ResearchTask 不属于当前 task")
    origin_research_task_id = str(source.metadata.get("research_task_id") or "")
    if origin_research_task_id and origin_research_task_id != research_task.id:
        raise ValueError("Candidate Evidence 不允许跨 ResearchTask 引用 SourceDocument")
    if page.source_id != source.id or chunk.source_id != source.id:
        raise ValueError("Candidate Evidence 的 source/page/chunk 引用不一致")
    if chunk.web_page_id != page.id or chunk.content_hash != page.content_hash:
        raise ValueError("Candidate Evidence 引用了过期或不匹配的网页版本")
    if page.text[chunk.source_text_start:chunk.source_text_end] != chunk.text:
        raise ValueError("SourceChunk 无法映射回当前 WebPageContent 原文")
    local_start = chunk.text.find(exact_quote)
    if local_start < 0:
        raise ValueError("Candidate Evidence quote 不存在于指定 SourceChunk")
    absolute_start = chunk.source_text_start + local_start
    absolute_end = absolute_start + len(exact_quote)
    if page.text[absolute_start:absolute_end] != exact_quote:
        raise ValueError("Candidate Evidence quote 无法逐字回放到网页原文")
    identity = (
        f"{task_id}\n{source.id}\n{page.content_hash}\n"
        f"{absolute_start}\n{absolute_end}"
    )
    evidence_id = f"ev_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    return SourceEvidence(
        id=evidence_id,
        task_id=task_id,
        source_id=source.id,
        competitor=research_task.competitor or source.competitor,
        dimension=normalize_dimension(research_task.dimension),
        snippet=exact_quote,
        normalized_fact=" ".join(exact_quote.split()),
        confidence=0.8,
        source_text_start=absolute_start,
        source_text_end=absolute_end,
        extraction_method="research_agent_candidate_verifier_v1",
        metadata={
            "web_page_id": page.id,
            "research_task_id": research_task.id,
            "source_url": source.url,
            "content_hash": page.content_hash,
            "source_chunk_id": chunk.id,
            "quote_verified": True,
            "candidate_supports": supports,
            "normalization_policy": "whitespace_only_no_new_facts",
        },
    )


def extract_evidence_from_page(
    *,
    task_id: str,
    research_task: ResearchTask,
    source: SourceDocument,
    page: WebPageContent,
    max_items: int = 8,
    scan_start: int = 0,
    scan_end: int | None = None,
    source_chunk_id: str = "",
    retrieval_run_id: str = "",
    retrieval_rank: int = 0,
) -> list[SourceEvidence]:
    scan_end = len(page.text) if scan_end is None else scan_end
    if scan_start < 0 or scan_end < scan_start or scan_end > len(page.text):
        raise ValueError("Extractor 扫描范围超出 WebPageContent 原文边界")
    scan_text = page.text[scan_start:scan_end]
    dimension = normalize_dimension(research_task.dimension)
    keywords = DIMENSION_KEYWORDS.get(
        dimension,
        ("支持", "提供", "适用于", "功能", "服务", "产品"),
    )
    intent_text = " ".join(
        [research_task.objective, *research_task.query_hints]
    ).casefold()
    intent_keywords = {
        keyword
        for values in DIMENSION_KEYWORDS.values()
        for keyword in values
        if keyword.casefold() in intent_text
    }
    candidate_map: dict[str, tuple[int, int, int, str]] = {}

    def add_candidate(raw_start: int, raw_end: int) -> None:
        raw = scan_text[raw_start:raw_end]
        snippet = raw.strip()
        if len(snippet) < 24 or snippet.startswith(("首页", "登录", "版权所有")):
            return
        if snippet == page.title or snippet == source.title:
            return
        if any(pattern in snippet for pattern in LOW_VALUE_PATTERNS):
            return
        if snippet.endswith(("？", "?")):
            return
        relative = raw.find(snippet)
        start = scan_start + raw_start + max(relative, 0)
        end = start + len(snippet)
        score = sum(keyword.casefold() in snippet.casefold() for keyword in keywords)
        intent_hit_count = sum(
            keyword.casefold() in snippet.casefold()
            for keyword in intent_keywords
        )
        if intent_keywords and intent_hit_count == 0:
            return
        score += intent_hit_count * 3
        if research_task.competitor and research_task.competitor in snippet:
            score += 1
        if re.search(r"\d", snippet) and any(
            marker in snippet
            for marker in ("支持", "最多", "上限", "价格", "费用", "元", "%", "秒", "人", "年")
        ):
            score += 1
        fingerprint = re.sub(r"\s+", "", snippet).casefold()
        candidate = (score, start, end, snippet)
        existing = candidate_map.get(fingerprint)
        if existing is None or score > existing[0]:
            candidate_map[fingerprint] = candidate

    for match in re.finditer(r"[^。！？!?\n]{20,500}[。！？!?]?", scan_text):
        add_candidate(match.start(), match.end())

    # DOM text often splits one factual sentence across adjacent short elements.
    # Join two or three neighboring non-empty lines while preserving the exact span.
    lines = list(re.finditer(r"[^\n]+", scan_text))
    for index in range(len(lines)):
        for width in (2, 3):
            end_index = index + width - 1
            if end_index >= len(lines):
                continue
            window_lines = [
                lines[item_index].group(0).strip()
                for item_index in range(index, end_index + 1)
            ]
            if not any(len(value) < 24 for value in window_lines):
                continue
            start = lines[index].start()
            end = lines[end_index].end()
            if end - start <= 500:
                add_candidate(start, end)

    candidates = list(candidate_map.values())
    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
    selected = [item for item in ranked if item[0] > 0][:max_items]
    if not selected:
        selected = ranked[: min(3, max_items)]

    evidence: list[SourceEvidence] = []
    seen: set[str] = set()
    for score, start, end, snippet in selected:
        fingerprint = re.sub(r"\s+", "", snippet).casefold()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        evidence.append(
            SourceEvidence(
                task_id=task_id,
                source_id=source.id,
                competitor=research_task.competitor or source.competitor,
                dimension=dimension,
                snippet=snippet,
                normalized_fact=" ".join(snippet.split()),
                confidence=min(0.9, 0.58 + score * 0.06),
                source_text_start=start,
                source_text_end=end,
                extraction_method="deterministic_quote_extractor_v1",
                metadata={
                    "web_page_id": page.id,
                    "research_task_id": research_task.id,
                    "source_url": source.url,
                    "content_hash": page.content_hash,
                    "quote_verified": page.text[start:end] == snippet,
                    "keyword_score": score,
                    "intent_keywords": sorted(intent_keywords),
                    "normalization_policy": "whitespace_only_no_new_facts",
                    "source_chunk_id": source_chunk_id,
                    "retrieval_run_id": retrieval_run_id,
                    "retrieval_rank": retrieval_rank,
                },
            )
        )
    return evidence


class WebEvidenceExtractorAgent(BaseAgent):
    """Convert collected page text into quote-level, source-linked evidence."""

    def __init__(self, *, store: ArtifactStore):
        super().__init__(
            name="web_evidence_extractor_agent",
            role=AgentRole.EXTRACTOR,
            input_artifacts=["sources", "web_pages", "research_tasks"],
            output_artifacts=["evidence", "evidence_extraction_attempts"],
        )
        self.store = store

    def execute(self, context: AgentContext) -> AgentResult:
        research_task = ResearchTask(**context.metadata["research_task"])
        source_rag_mode = str(context.metadata.get("source_rag_mode") or "off")
        retrieval_run_id = str(context.metadata.get("retrieval_run_id") or "")
        source_ids = set(context.metadata.get("source_ids") or [])
        sources = [
            SourceDocument(**item)
            for item in self.store.load_many(context.task_id, "sources")
            if not source_ids or item.get("id") in source_ids
        ]
        source_by_id = {item.id: item for item in sources}
        pages = [
            WebPageContent(**item)
            for item in self.store.load_many(context.task_id, "web_pages")
            if item.get("source_id") in source_by_id
        ]
        page_by_id = {item.id: item for item in pages}
        current_hash_by_source = {
            item.source_id: item.content_hash for item in pages
        }
        existing = [
            SourceEvidence(**item)
            for item in self.store.load_many(context.task_id, "evidence")
        ]
        attempts = [
            EvidenceExtractionAttempt(**item)
            for item in self.store.load_many(context.task_id, "evidence_extraction_attempts")
        ]
        page_candidates: dict[str, list[SourceEvidence]] = {}
        page_chunk_ids: dict[str, list[str]] = {}
        scanned_page_ids: list[str] = []

        if source_rag_mode != "off":
            selected_chunk_ids = list(
                context.metadata.get("source_chunk_ids") or []
            )
            if not selected_chunk_ids:
                raise ValueError("Source RAG 没有返回可供 Extractor 扫描的 chunk")
            selected_id_set = set(selected_chunk_ids)
            chunk_by_id = {
                item.id: item
                for item in (
                    SourceChunk(**raw)
                    for raw in self.store.load_many(context.task_id, "source_chunks")
                    if raw.get("id") in selected_id_set
                )
            }
            missing_chunk_ids = [
                item for item in selected_chunk_ids if item not in chunk_by_id
            ]
            if missing_chunk_ids:
                raise ValueError(
                    f"Source RAG 选择了不存在的 chunk：{missing_chunk_ids}"
                )
            for rank, chunk_id in enumerate(selected_chunk_ids, start=1):
                chunk = chunk_by_id[chunk_id]
                source = source_by_id.get(chunk.source_id)
                page = page_by_id.get(chunk.web_page_id)
                if source is None or page is None:
                    raise ValueError("SourceChunk 超出当前 Extractor source_ids 范围")
                if chunk.task_id != context.task_id or page.content_hash != chunk.content_hash:
                    raise ValueError("SourceChunk 不属于当前 task 或不是当前网页版本")
                if page.text[chunk.source_text_start:chunk.source_text_end] != chunk.text:
                    raise ValueError("SourceChunk 无法映射回 WebPageContent 原文")
                if page.id not in scanned_page_ids:
                    scanned_page_ids.append(page.id)
                page_chunk_ids.setdefault(page.id, []).append(chunk.id)
                page_candidates.setdefault(page.id, []).extend(
                    extract_evidence_from_page(
                        task_id=context.task_id,
                        research_task=research_task,
                        source=source,
                        page=page,
                        scan_start=chunk.source_text_start,
                        scan_end=chunk.source_text_end,
                        source_chunk_id=chunk.id,
                        retrieval_run_id=retrieval_run_id,
                        retrieval_rank=rank,
                    )
                )
        else:
            for page in pages:
                source = source_by_id[page.source_id]
                scanned_page_ids.append(page.id)
                page_candidates[page.id] = extract_evidence_from_page(
                    task_id=context.task_id,
                    research_task=research_task,
                    source=source,
                    page=page,
                )

        existing_by_key = {
            evidence_span_identity(item): item
            for item in existing
            if item.source_id in source_by_id
            and str(item.metadata.get("content_hash") or "")
            == current_hash_by_source.get(item.source_id, "")
        }
        resolved_evidence: list[SourceEvidence] = []
        persisted_new_evidence: list[SourceEvidence] = []
        new_attempts: list[EvidenceExtractionAttempt] = []
        resolved_ids: set[str] = set()
        for page_id in scanned_page_ids:
            page = page_by_id[page_id]
            source = source_by_id[page.source_id]
            unique_candidates: dict[
                tuple[str, str, int, int], SourceEvidence
            ] = {}
            for item in page_candidates.get(page.id, []):
                key = evidence_span_identity(item)
                current = unique_candidates.get(key)
                if current is None or item.confidence > current.confidence:
                    unique_candidates[key] = item
            selected_items = sorted(
                unique_candidates.values(),
                key=lambda item: (
                    -item.confidence,
                    item.source_text_start,
                    item.source_text_end,
                    item.id,
                ),
            )[:8]
            page_resolved: list[SourceEvidence] = []
            for item in selected_items:
                key = evidence_span_identity(item)
                resolved = existing_by_key.get(key)
                if resolved is None:
                    existing_by_key[key] = item
                    persisted_new_evidence.append(item)
                    resolved = item
                if resolved.id not in resolved_ids:
                    resolved_ids.add(resolved.id)
                    resolved_evidence.append(resolved)
                page_resolved.append(resolved)
            new_attempts.append(
                EvidenceExtractionAttempt(
                    task_id=context.task_id,
                    research_task_id=research_task.id,
                    source_id=source.id,
                    web_page_id=page.id,
                    status="completed" if page_resolved else "failed",
                    extraction_method=(
                        f"deterministic_quote_extractor_{source_rag_mode}"
                        if source_rag_mode != "off"
                        else "deterministic_quote_extractor_v1"
                    ),
                    evidence_ids=[item.id for item in page_resolved],
                    extracted_count=len(page_resolved),
                    error=(
                        ""
                        if page_resolved
                        else "没有找到满足最小长度的可引用正文句子"
                    ),
                    metadata={
                        "source_rag_mode": source_rag_mode,
                        "retrieval_run_id": retrieval_run_id,
                        "source_chunk_ids": page_chunk_ids.get(page.id, []),
                    },
                )
            )
        self.store.save_many(
            context.task_id,
            "evidence",
            existing + persisted_new_evidence,
        )
        self.store.save_many(
            context.task_id,
            "evidence_extraction_attempts",
            attempts + new_attempts,
        )
        if not resolved_evidence:
            raise ValueError("没有从目标 WebPageContent（网页正文）中抽取到证据")
        validate_source_evidence_links(sources, resolved_evidence)
        for item in resolved_evidence:
            page = next(
                candidate
                for candidate in pages
                if candidate.source_id == item.source_id
                and candidate.content_hash == item.metadata.get("content_hash")
            )
            if (
                page.text[item.source_text_start:item.source_text_end]
                != item.snippet
                or not item.metadata.get("quote_verified")
            ):
                raise ValueError("存在无法在原始网页正文中逐字定位的证据")
        scan_scope = (
            f"{len(context.metadata.get('source_chunk_ids') or [])} 个 chunks"
            if source_rag_mode != "off"
            else "全文"
        )
        return self.make_result(
            context,
            output_summary=(
                f"从 {len(scanned_page_ids)} 个网页的"
                f"{scan_scope}"
                f"扫描范围解析 {len(resolved_evidence)} 条可引用证据，"
                f"其中新增 {len(persisted_new_evidence)} 条"
            ),
            output_artifacts={
                "evidence": [item.id for item in resolved_evidence],
                "evidence_extraction_attempts": [item.id for item in new_attempts],
            },
        )
