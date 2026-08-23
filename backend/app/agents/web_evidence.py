from __future__ import annotations

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
    lowered = value.casefold()
    mappings = (
        (EvidenceDimension.PRICING.value, ("价格", "定价", "成本", "pricing")),
        (EvidenceDimension.FEATURE.value, ("能力", "功能", "feature")),
        (EvidenceDimension.ECOSYSTEM.value, ("生态", "集成", "api", "sdk")),
        (EvidenceDimension.POSITIONING.value, ("定位", "position")),
        (EvidenceDimension.CUSTOMER.value, ("客户", "用户", "customer")),
        (EvidenceDimension.RISK.value, ("风险", "安全", "合规", "部署", "责任", "risk")),
    )
    for dimension, aliases in mappings:
        if any(alias in lowered for alias in aliases):
            return dimension
    return EvidenceDimension.OTHER.value


def extract_evidence_from_page(
    *,
    task_id: str,
    research_task: ResearchTask,
    source: SourceDocument,
    page: WebPageContent,
    max_items: int = 8,
) -> list[SourceEvidence]:
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
        raw = page.text[raw_start:raw_end]
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
        start = raw_start + max(relative, 0)
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

    for match in re.finditer(r"[^。！？!?\n]{20,500}[。！？!?]?", page.text):
        add_candidate(match.start(), match.end())

    # DOM text often splits one factual sentence across adjacent short elements.
    # Join two or three neighboring non-empty lines while preserving the exact span.
    lines = list(re.finditer(r"[^\n]+", page.text))
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
        existing = [
            SourceEvidence(**item)
            for item in self.store.load_many(context.task_id, "evidence")
        ]
        attempts = [
            EvidenceExtractionAttempt(**item)
            for item in self.store.load_many(context.task_id, "evidence_extraction_attempts")
        ]
        new_evidence: list[SourceEvidence] = []
        new_attempts: list[EvidenceExtractionAttempt] = []
        for page in pages:
            source = source_by_id[page.source_id]
            items = extract_evidence_from_page(
                task_id=context.task_id,
                research_task=research_task,
                source=source,
                page=page,
            )
            new_evidence.extend(items)
            new_attempts.append(
                EvidenceExtractionAttempt(
                    task_id=context.task_id,
                    research_task_id=research_task.id,
                    source_id=source.id,
                    web_page_id=page.id,
                    status="completed" if items else "failed",
                    extraction_method="deterministic_quote_extractor_v1",
                    evidence_ids=[item.id for item in items],
                    extracted_count=len(items),
                    error="" if items else "没有找到满足最小长度的可引用正文句子",
                )
            )
        self.store.save_many(context.task_id, "evidence", existing + new_evidence)
        self.store.save_many(
            context.task_id,
            "evidence_extraction_attempts",
            attempts + new_attempts,
        )
        if not new_evidence:
            raise ValueError("没有从目标 WebPageContent（网页正文）中抽取到证据")
        validate_source_evidence_links(sources, new_evidence)
        if not all(item.metadata.get("quote_verified") for item in new_evidence):
            raise ValueError("存在无法在原始网页正文中逐字定位的证据")
        return self.make_result(
            context,
            output_summary=f"从 {len(pages)} 个网页抽取 {len(new_evidence)} 条可引用证据",
            output_artifacts={
                "evidence": [item.id for item in new_evidence],
                "evidence_extraction_attempts": [item.id for item in new_attempts],
            },
        )
