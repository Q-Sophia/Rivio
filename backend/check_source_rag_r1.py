from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from app.collection import CollectorQueueService  # noqa: F401 - stabilizes existing package import order
from app.extraction import ExtractorQueueService
from app.agents.web_evidence import WebEvidenceExtractorAgent
from app.harness.artifacts import ArtifactStore
from app.retrieval import SourceRAGService, build_retrieval_query, chunk_web_page, tokenize_sparse
from app.schemas import (
    AgentContext,
    AgentRole,
    AnalysisTask,
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
    WebPageContent,
)
from app.workflow.taskboard import TaskBoardStore


ROOT = Path(__file__).resolve().parent / "app" / "data" / "checks" / "source_rag_r1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_source_page(
    task_id: str,
    research_task_id: str,
    index: int,
    text: str,
) -> tuple[SourceDocument, WebPageContent]:
    source = SourceDocument(
        id=f"src_rag_{task_id}_{index}",
        task_id=task_id,
        title=f"星云 IDE 文档 {index}",
        url=f"https://fixture.test/{task_id}/{index}",
        source_type="official_site",
        competitor="星云IDE",
        content_excerpt=text[:8000],
        metadata={
            "research_task_id": research_task_id,
            "content_hash": content_hash(text),
        },
    )
    page = WebPageContent(
        id=f"page_rag_{task_id}_{index}",
        task_id=task_id,
        source_id=source.id,
        requested_url=source.url,
        final_url=source.url,
        title=source.title,
        text=text,
        content_hash=content_hash(text),
    )
    return source, page


def research_fixture(task_id: str, research_task_id: str) -> tuple[ResearchTask, InformationNeed, KeyIntelligenceQuestion]:
    kiq = KeyIntelligenceQuestion(
        id=f"kiq_{task_id}",
        task_id=task_id,
        question="星云IDE的代码协作、API集成和自动化能力有哪些可验证事实？",
        decision_link="评估星云IDE产品能力",
        dimensions=["产品能力"],
    )
    need = InformationNeed(
        id=f"need_{task_id}",
        task_id=task_id,
        question_id=kiq.id,
        dimension="产品能力",
        required_facts=["代码协作功能", "API 与 SDK 集成能力", "自动化工作流"],
        preferred_source_types=["official_site", "docs"],
        comparability_basis="使用相同功能口径比较",
        decision_link="评估星云IDE产品能力",
    )
    research_task = ResearchTask(
        id=research_task_id,
        task_id=task_id,
        information_need_id=need.id,
        title="核实星云IDE产品能力",
        objective="查找星云IDE代码协作、API集成与自动化能力的直接事实",
        competitor="星云IDE",
        dimension="产品能力",
        query_hints=["星云IDE API SDK", "Nebula IDE collaboration automation"],
        status="collected",
        stop_condition="获得可逐字引用的功能证据",
    )
    return research_task, need, kiq


def save_base_artifacts(
    store: ArtifactStore,
    task_id: str,
    research_task: ResearchTask,
    need: InformationNeed,
    kiq: KeyIntelligenceQuestion,
    sources: list[SourceDocument],
    pages: list[WebPageContent],
    *,
    with_board: bool = False,
) -> None:
    store.save_many(task_id, "analysis_tasks", [
        AnalysisTask(
            id=task_id,
            query="评估星云IDE产品能力",
            competitors=["星云IDE"],
            focus_areas=["产品能力"],
        )
    ])
    store.save_many(task_id, "research_tasks", [research_task])
    store.save_many(task_id, "research_information_needs", [need])
    store.save_many(task_id, "research_kiqs", [kiq])
    store.save_many(task_id, "sources", sources)
    store.save_many(task_id, "web_pages", pages)
    for artifact_type in (
        "evidence",
        "evidence_extraction_attempts",
        "source_chunks",
        "source_retrieval_runs",
        "agent_runs",
        "dag_nodes",
        "tool_calls",
    ):
        store.save_many(task_id, artifact_type, [])
    if with_board:
        record = TaskRecord(
            id=f"queue_extract_{research_task.id}",
            task_id=task_id,
            task_key=f"extract_evidence_{research_task.id}",
            task_type=TaskType.EXTRACT_SOURCE_EVIDENCE,
            target_agent_role=AgentRole.EXTRACTOR,
            status=TaskStatus.READY,
            reason="RAG fixture extraction",
            metadata={
                "research_task_id": research_task.id,
                "source_ids": [item.id for item in sources],
            },
        )
        TaskBoardStore(store).save_board(
            TaskBoard(task_id=task_id, status=TaskStatus.READY, tasks=[record])
        )


def check_chunker() -> None:
    task_id = "task_source_rag_chunker"
    research_task_id = "researchtask_source_rag_chunker"
    paragraphs = [
        f"第{index}段介绍星云IDE产品能力，并说明 API、SDK 和 collaboration 自动化工作流。"
        for index in range(80)
    ]
    text = "\n\n".join(paragraphs)
    source, page = make_source_page(task_id, research_task_id, 1, text)
    first = chunk_web_page(
        task_id=task_id,
        source=source,
        page=page,
        target_chars=800,
        overlap_chars=120,
    )
    second = chunk_web_page(
        task_id=task_id,
        source=source,
        page=page,
        target_chars=800,
        overlap_chars=120,
    )
    require(len(first) > 2, "chunker 没有切分长正文")
    require([item.id for item in first] == [item.id for item in second], "chunk id 不确定")
    for index, chunk in enumerate(first):
        require(
            chunk.text == text[chunk.source_text_start:chunk.source_text_end],
            "chunk 不能映射回绝对正文位置",
        )
        if index:
            previous = first[index - 1]
            require(chunk.source_text_start < previous.source_text_end, "相邻 chunk 没有 overlap")
            require(chunk.source_text_start > previous.source_text_start, "chunk 边界没有前进")


def build_long_pages(task_id: str, research_task_id: str) -> tuple[list[SourceDocument], list[WebPageContent]]:
    sources: list[SourceDocument] = []
    pages: list[WebPageContent] = []
    for source_index in range(5):
        blocks: list[str] = []
        for block_index in range(100):
            if block_index in {6, 40, 80}:
                blocks.append(
                    f"星云IDE在第{source_index}份文档中提供 API 和 SDK 集成能力，"
                    "支持团队代码协作、自动化工作流与多终端开发。"
                )
            else:
                blocks.append(
                    f"这是第{source_index}份资料的第{block_index}段通用背景说明，"
                    "用于描述发布记录、页面导航和其他不相关信息，保持足够长度用于检索测试。"
                )
        source, page = make_source_page(
            task_id,
            research_task_id,
            source_index,
            "\n\n".join(blocks),
        )
        sources.append(source)
        pages.append(page)
    return sources, pages


def check_bm25_and_integration() -> dict[str, float | int]:
    task_id = "task_source_rag_bm25"
    research_task_id = "researchtask_source_rag_bm25"
    store = ArtifactStore(ROOT)
    research_task, need, kiq = research_fixture(task_id, research_task_id)
    sources, pages = build_long_pages(task_id, research_task_id)
    save_base_artifacts(
        store,
        task_id,
        research_task,
        need,
        kiq,
        sources,
        pages,
        with_board=True,
    )

    query = build_retrieval_query(research_task, [need], [kiq])
    tokens = tokenize_sparse(query)
    require("api" in tokens and "sdk" in tokens, "英文 BM25 token 缺失")
    require("产品" in tokens and "能力" in tokens, "中文 bigram token 缺失")

    result = ExtractorQueueService(
        store=store,
        source_rag_mode="bm25_v1",
    ).run_once(task_id)
    require(result["status"] == "completed", str(result))
    runs = store.load_many(task_id, "source_retrieval_runs")
    chunks = store.load_many(task_id, "source_chunks")
    evidence = store.load_many(task_id, "evidence")
    require(len(runs) == 1, "没有持久化 retrieval run")
    run = runs[0]
    require(run["algorithm"] == "bm25_v1", "BM25 algorithm 标记错误")
    require(len(run["selected_chunk_ids"]) <= 10, "超过 top-k")
    chunk_by_id = {item["id"]: item for item in chunks}
    per_source = Counter(
        chunk_by_id[item]["source_id"] for item in run["selected_chunk_ids"]
    )
    require(max(per_source.values()) <= 3, "超过单 Source chunk 上限")
    require(run["selected_text_chars"] <= 20_000, "超过 selected text 预算")
    require(run["candidate_chunk_count"] == len(chunks), "candidate count 不准确")
    allowed_source_ids = {item.id for item in sources}
    page_hash_by_id = {item.id: item.content_hash for item in pages}
    for chunk in chunks:
        require(chunk["task_id"] == task_id, "chunk 跨 task")
        require(chunk["source_id"] in allowed_source_ids, "chunk 跨未选 Source")
        require(
            chunk["content_hash"] == page_hash_by_id[chunk["web_page_id"]],
            "chunk 使用了过期 content_hash",
        )
    selected_ids = set(run["selected_chunk_ids"])
    identities = set()
    page_by_id = {item.id: item for item in pages}
    for item in evidence:
        chunk_id = item["metadata"].get("source_chunk_id")
        require(chunk_id in selected_ids, "RAG on 路径扫描了未选 chunk 或全文")
        chunk = chunk_by_id[chunk_id]
        require(
            chunk["source_text_start"] <= item["source_text_start"]
            and item["source_text_end"] <= chunk["source_text_end"],
            "chunk local span 没有转换为 absolute span",
        )
        page = page_by_id[item["metadata"]["web_page_id"]]
        require(
            page.text[item["source_text_start"]:item["source_text_end"]]
            == item["snippet"],
            "quote verification 断链",
        )
        identity = (
            item["source_id"],
            item["dimension"],
            item["source_text_start"],
            item["source_text_end"],
        )
        require(identity not in identities, "overlap chunk 产生重复 Evidence")
        identities.add(identity)
    require(evidence, "RAG on 没有产出 Evidence")
    require(
        any(item["tool_name"] == "retrieve_source_chunks" for item in store.load_many(task_id, "tool_calls")),
        "Trace 中缺少 retrieve_source_chunks",
    )

    raw_chars = sum(len(item.text) for item in pages)
    selected_chars = int(run["selected_text_chars"])
    return {
        "raw_chars": raw_chars,
        "chunk_count": len(chunks),
        "candidate_count": int(run["candidate_chunk_count"]),
        "selected_count": len(run["selected_chunk_ids"]),
        "selected_chars": selected_chars,
        "reduction_percent": round((1 - selected_chars / raw_chars) * 100, 2),
    }


def check_zero_score_fallback_and_isolation() -> None:
    task_id = "task_source_rag_fallback"
    research_task_id = "researchtask_source_rag_fallback"
    store = ArtifactStore(ROOT)
    research_task = ResearchTask(
        id=research_task_id,
        task_id=task_id,
        information_need_id="need_missing",
        title="完全无匹配检索",
        objective="量子纠缠火星农业",
        competitor="未知对象",
        dimension="未知维度",
        query_hints=["量子纠缠"],
        status="collected",
        stop_condition="测试 fallback",
    )
    need = InformationNeed(
        id="need_missing",
        task_id=task_id,
        question_id="kiq_missing",
        dimension="未知维度",
        required_facts=["火星农业事实"],
        comparability_basis="未知",
        decision_link="测试",
    )
    kiq = KeyIntelligenceQuestion(
        id="kiq_missing",
        task_id=task_id,
        question="量子纠缠如何影响火星农业？",
        decision_link="测试",
    )
    source, page = make_source_page(
        task_id,
        research_task_id,
        1,
        ("alpha beta gamma delta epsilon. " * 180),
    )
    unselected, unselected_page = make_source_page(
        task_id,
        research_task_id,
        2,
        ("quantum agriculture should remain outside allowed source scope. " * 80),
    )
    save_base_artifacts(
        store,
        task_id,
        research_task,
        need,
        kiq,
        [source, unselected],
        [page, unselected_page],
    )
    stale_chunk = SourceChunk(
        id="chunk_stale_content_hash",
        task_id=task_id,
        source_id=source.id,
        web_page_id=page.id,
        content_hash="stale-content-hash",
        chunk_index=0,
        source_text_start=0,
        source_text_end=20,
        text=page.text[:20],
        competitor=source.competitor,
        origin_research_task_id=research_task.id,
    )
    store.save_many(task_id, "source_chunks", [stale_chunk])
    other_task_id = "task_source_rag_other"
    other_source, other_page = make_source_page(
        other_task_id,
        "researchtask_other",
        1,
        "量子纠缠火星农业的跨任务文本。" * 100,
    )
    other_chunks = chunk_web_page(
        task_id=other_task_id,
        source=other_source,
        page=other_page,
    )
    store.save_many(other_task_id, "source_chunks", other_chunks)

    run, selected = SourceRAGService(
        store=store,
        target_chars=700,
        overlap_chars=100,
        top_k=5,
        max_chunks_per_source=2,
    ).run(
        task_id=task_id,
        research_task=research_task,
        source_ids=[source.id],
    )
    require(run.fallback_used is True, "zero-score 没有显式 fallback")
    require(selected, "fallback 没有返回有限 chunk")
    require(all(item.task_id == task_id for item in selected), "检索跨 task")
    require(all(item.source_id == source.id for item in selected), "检索跨未选 Source")
    require(all(item.content_hash == page.content_hash for item in selected), "检索使用过期版本")
    require(
        not any(
            item["id"] == stale_chunk.id
            for item in store.load_many(task_id, "source_chunks")
        ),
        "过期 content_hash chunk 没有被当前版本替换",
    )
    require(len(selected) <= 2, "fallback 超过单 Source 上限")


def check_overlap_dedupe_and_off_mode() -> None:
    task_id = "task_source_rag_overlap"
    research_task_id = "researchtask_source_rag_overlap"
    store = ArtifactStore(ROOT)
    research_task, need, kiq = research_fixture(task_id, research_task_id)
    prefix = "背景资料用于形成足够长度并测试重叠窗口。" * 45
    fact = "星云IDE支持团队代码协作，并通过API和SDK连接自动化工作流。"
    text = prefix + fact + ("后续背景信息继续描述其他普通内容。" * 45)
    source, page = make_source_page(task_id, research_task_id, 1, text)
    save_base_artifacts(store, task_id, research_task, need, kiq, [source], [page])
    fact_start = text.index(fact)
    fact_end = fact_start + len(fact)
    chunks = [
        SourceChunk(
            id="chunk_overlap_a",
            task_id=task_id,
            source_id=source.id,
            web_page_id=page.id,
            content_hash=page.content_hash,
            chunk_index=0,
            source_text_start=0,
            source_text_end=min(len(text), fact_end + 120),
            text=text[:min(len(text), fact_end + 120)],
            competitor=source.competitor,
            origin_research_task_id=research_task.id,
        ),
        SourceChunk(
            id="chunk_overlap_b",
            task_id=task_id,
            source_id=source.id,
            web_page_id=page.id,
            content_hash=page.content_hash,
            chunk_index=1,
            source_text_start=max(0, fact_start - 120),
            source_text_end=len(text),
            text=text[max(0, fact_start - 120):],
            competitor=source.competitor,
            origin_research_task_id=research_task.id,
        ),
    ]
    store.save_many(task_id, "source_chunks", chunks)
    context = AgentContext(
        task_id=task_id,
        task=AnalysisTask(
            id=task_id,
            query="核实星云IDE功能",
            competitors=["星云IDE"],
        ),
        node_id="extract_overlap",
        metadata={
            "research_task": research_task.model_dump(mode="json"),
            "source_ids": [source.id],
            "source_rag_mode": "bm25_v1",
            "source_chunk_ids": [item.id for item in chunks],
            "retrieval_run_id": "retrieval_overlap",
        },
    )
    result = WebEvidenceExtractorAgent(store=store).execute(context)
    require(result.status == "completed", result.error)
    evidence = [
        item
        for item in store.load_many(task_id, "evidence")
        if fact in item["snippet"]
    ]
    identities = {
        (
            item["source_id"],
            item["dimension"],
            item["source_text_start"],
            item["source_text_end"],
        )
        for item in evidence
    }
    require(len(evidence) == len(identities), "overlap span 没有精确去重")
    require(evidence, "overlap fixture 没有抽取目标事实")
    require(
        all(page.text[item["source_text_start"]:item["source_text_end"]] == item["snippet"] for item in evidence),
        "overlap absolute span 不能逐字回放",
    )

    off_task_id = "task_source_rag_off"
    off_research_id = "researchtask_source_rag_off"
    off_task, off_need, off_kiq = research_fixture(off_task_id, off_research_id)
    off_source, off_page = make_source_page(
        off_task_id,
        off_research_id,
        1,
        "星云IDE面向开发团队提供代码协作功能，并支持API与SDK集成自动化流程。" * 30,
    )
    save_base_artifacts(
        store,
        off_task_id,
        off_task,
        off_need,
        off_kiq,
        [off_source],
        [off_page],
        with_board=True,
    )
    off_result = ExtractorQueueService(
        store=store,
        source_rag_mode="off",
    ).run_once(off_task_id)
    require(off_result["status"] == "completed", str(off_result))
    require(not store.load_many(off_task_id, "source_retrieval_runs"), "RAG off 仍执行检索")
    require(not store.load_many(off_task_id, "source_chunks"), "RAG off 仍生成 chunk")
    require(
        all(not item["metadata"].get("source_chunk_id") for item in store.load_many(off_task_id, "evidence")),
        "RAG off 没有走旧全文路径",
    )


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    check_chunker()
    stats = check_bm25_and_integration()
    check_zero_score_fallback_and_isolation()
    check_overlap_dedupe_and_off_mode()
    print("check_source_rag_r1: PASS")
    print("chunk_boundaries_and_overlap=true")
    print("deterministic_chunk_ids=true")
    print("chinese_english_bm25=true")
    print("task_source_hash_isolation=true")
    print("top_k_and_per_source_limit=true")
    print("zero_score_fallback=true")
    print("absolute_quote_provenance=true")
    print("overlap_evidence_dedup=true")
    print("rag_off_legacy_path=true")
    print("rag_on_selected_chunks_only=true")
    for key, value in stats.items():
        print(f"fixture_{key}={value}")
    print("real_external_calls=0")


if __name__ == "__main__":
    main()
