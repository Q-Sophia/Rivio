from __future__ import annotations

import json
from pathlib import Path

from app.collection import CollectorQueueService  # noqa: F401 - stabilize package import order
from app.extraction import ExtractorQueueService
from app.harness.artifacts import ArtifactStore
from app.retrieval import (
    CrossEncoderReranker,
    DenseRetriever,
    FinalSelector,
    ReciprocalRankFusion,
    RetrievalCandidate,
    SourceRAGService,
    clear_dense_embedding_cache,
    normalize_source_rag_mode,
)
from app.retrieval.hybrid import FastEmbedDenseBackend, FastEmbedRerankerBackend
from app.schemas import SourceChunk, SourceRetrievalRun
from check_source_rag_r1 import (
    build_long_pages,
    make_source_page,
    research_fixture,
    save_base_artifacts,
)


ROOT = Path(__file__).resolve().parent / "app" / "data" / "checks" / "source_rag_hybrid_rerank"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeDenseBackend:
    model_name = "fake-bge-m3"

    def __init__(self):
        self.corpus_calls: list[list[str]] = []

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.5, 0.25] for _ in texts]

    def encode_corpus(self, texts: list[str]) -> list[list[float]]:
        self.corpus_calls.append(list(texts))
        return [
            [
                1.0 if "semantic-only" in text or "API 和 SDK" in text else 0.05,
                1.0 if "代码协作" in text else 0.1,
                (sum(ord(char) for char in text[:80]) % 97) / 100.0,
            ]
            for text in texts
        ]


class FakeRerankerBackend:
    model_name = "fake-bge-reranker-v2-m3"

    def __init__(self):
        self.batches: list[list[str]] = []

    def compute_scores(self, query: str, passages: list[str]) -> list[float]:
        self.batches.append(list(passages))
        return [
            (20.0 if "API 和 SDK" in passage else 0.0)
            + (10.0 if "rerank-priority" in passage else 0.0)
            + index / 1000.0
            for index, passage in enumerate(passages)
        ]


class FailingDenseBackend(FakeDenseBackend):
    model_name = "fake-dense-failure"

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("intentional dense failure")


def chunk(chunk_id: str, source_id: str, text: str, *, content_hash: str = "hash") -> SourceChunk:
    return SourceChunk(
        id=chunk_id,
        task_id="task_hybrid_unit",
        source_id=source_id,
        web_page_id=f"page_{source_id}",
        content_hash=content_hash,
        chunk_index=0,
        source_text_start=0,
        source_text_end=len(text),
        text=text,
    )


def check_dense_cache_and_semantic_recall() -> None:
    clear_dense_embedding_cache()
    backend = FakeDenseBackend()
    lexical = chunk("chunk_lexical", "source_a", "普通 lexical passage")
    semantic = chunk("chunk_semantic", "source_b", "semantic-only concept")
    retriever = DenseRetriever(backend)
    first = retriever.retrieve("query", [lexical, semantic], top_k=2)
    require(first.candidates[0].chunk_id == semantic.id, "Dense 没召回语义候选")
    require(first.cache_hits == 0 and first.cache_misses == 2, "首次 cache 统计错误")
    second = retriever.retrieve("query", [lexical, semantic], top_k=2)
    require(second.cache_hits == 2 and second.cache_misses == 0, "进程 cache 未复用")
    require(
        [(item.chunk_id, item.dense_score) for item in first.candidates]
        == [(item.chunk_id, item.dense_score) for item in second.candidates],
        "Dense 排序/分数不确定",
    )
    bm25_only = [RetrievalCandidate(lexical, bm25_score=1.0, bm25_rank=1)]
    fused = ReciprocalRankFusion(k=60).fuse(bm25_only, first.candidates)
    require(semantic.id in {item.chunk_id for item in fused}, "Dense 未补回 BM25 漏掉的候选")
    changed = semantic.model_copy(update={"content_hash": "changed-hash"})
    third = retriever.retrieve("query", [lexical, changed], top_k=2)
    require(third.cache_hits == 1 and third.cache_misses == 1, "cache key 缺少 content_hash")


def check_rrf_and_rerank_contract() -> None:
    a = chunk("chunk_a", "source_a", "A")
    b = chunk("chunk_b", "source_b", "B")
    c = chunk("chunk_c", "source_c", "C rerank-priority")
    bm25 = [
        RetrievalCandidate(a, bm25_score=9.0, bm25_rank=1),
        RetrievalCandidate(b, bm25_score=8.0, bm25_rank=2),
    ]
    dense = [
        RetrievalCandidate(c, dense_score=0.9, dense_rank=1),
        RetrievalCandidate(b, dense_score=0.8, dense_rank=2),
    ]
    fused = ReciprocalRankFusion(k=60).fuse(bm25, dense, top_k=30)
    require(fused[0].chunk_id == b.id, "RRF 没有按 chunk_id 合并双路命中")
    expected = 1 / 62 + 1 / 62
    require(abs(float(fused[0].rrf_score) - expected) < 1e-12, "RRF k/rank 公式错误")
    require([item.rrf_rank for item in fused] == [1, 2, 3], "RRF 不是 1-based rank")
    tie = ReciprocalRankFusion(k=60).fuse(
        [RetrievalCandidate(c, bm25_rank=1), RetrievalCandidate(a, bm25_rank=1)],
        [],
    )
    require([item.chunk_id for item in tie] == ["chunk_a", "chunk_c"], "RRF tie 不确定")

    backend = FakeRerankerBackend()
    reranked = CrossEncoderReranker(backend).rerank("query", fused)
    require(len(backend.batches[0]) == len(fused) <= 30, "Reranker 输入不是 fusion Top-30")
    require(reranked[0].chunk_id == c.id, "CrossEncoder 没有改变 fusion 排序")
    require([item.rerank_rank for item in reranked] == list(range(1, len(reranked) + 1)), "rerank rank 错误")

    many = [chunk(f"chunk_many_{index:02}", f"source_many_{index}", f"passage {index}") for index in range(40)]
    many_bm25 = [RetrievalCandidate(item, bm25_rank=index + 1) for index, item in enumerate(many[:20])]
    many_dense = [RetrievalCandidate(item, dense_rank=index + 1) for index, item in enumerate(many[20:])]
    fusion_top30 = ReciprocalRankFusion(k=60).fuse(many_bm25, many_dense, top_k=30)
    bounded_backend = FakeRerankerBackend()
    CrossEncoderReranker(bounded_backend).rerank("query", fusion_top30)
    require(len(fusion_top30) == len(bounded_backend.batches[0]) == 30, "Reranker 不是严格只接收 fusion Top-30")


def check_final_selector_limits() -> None:
    candidates = []
    for index in range(16):
        source_id = f"source_{index // 4}"
        item = chunk(f"chunk_limit_{index:02}", source_id, "x" * 1800)
        candidates.append(RetrievalCandidate(item, rrf_score=1.0, rrf_rank=index + 1))
    selected = FinalSelector().select(candidates)
    require(len(selected) == 10, "FinalSelector 未应用 Top-10")
    counts: dict[str, int] = {}
    for item in selected:
        counts[item.source_id] = counts.get(item.source_id, 0) + 1
    require(max(counts.values()) <= 3, "FinalSelector 超过每 Source 3 chunks")
    require(sum(len(item.chunk.text) for item in selected) <= 20_000, "FinalSelector 超过 20k")


def check_hybrid_extractor_and_artifact() -> None:
    task_id = "task_source_rag_hybrid_integration"
    research_task_id = "researchtask_source_rag_hybrid_integration"
    store = ArtifactStore(ROOT)
    research_task, need, kiq = research_fixture(task_id, research_task_id)
    sources, pages = build_long_pages(task_id, research_task_id)
    pages[0] = pages[0].model_copy(update={"text": pages[0].text + "\nrerank-priority"})
    pages[0] = pages[0].model_copy(update={"content_hash": __import__("hashlib").sha256(pages[0].text.encode()).hexdigest()})
    sources[0] = sources[0].model_copy(update={"metadata": {**sources[0].metadata, "content_hash": pages[0].content_hash}})
    save_base_artifacts(store, task_id, research_task, need, kiq, sources, pages, with_board=True)
    clear_dense_embedding_cache()
    dense = FakeDenseBackend()
    reranker = FakeRerankerBackend()
    result = ExtractorQueueService(
        store=store,
        source_rag_mode="hybrid_rerank_v1",
        dense_backend=dense,
        reranker_backend=reranker,
    ).run_once(task_id)
    require(result["status"] == "completed", str(result))
    raw_run = store.load_many(task_id, "source_retrieval_runs")[-1]
    run = SourceRetrievalRun(**raw_run)
    require(run.retrieval_mode == "hybrid_rerank_v1", "retrieval mode Artifact 错误")
    require(run.embedding_model == dense.model_name, "embedding model 未记录")
    require(run.reranker_model == reranker.model_name, "reranker model 未记录")
    require(run.bm25_candidate_count <= 20, "BM25 hybrid 分支超过 Top-20")
    require(run.dense_candidate_count <= 20, "Dense 分支超过 Top-20")
    require(run.fusion_candidate_count <= 30, "RRF 超过 Top-30")
    require(run.reranked_count == run.fusion_candidate_count, "Reranker 未消费 fusion candidates")
    require(run.selected_count <= 10, "Final selection 超过 Top-10")
    require(len(reranker.batches) == 1 and len(reranker.batches[0]) <= 30, "Reranker 调用范围错误")
    serialized = json.dumps(raw_run, ensure_ascii=False)
    require("dense_vecs" not in serialized and "embedding_vector" not in serialized, "Artifact 泄露 embedding vector")
    tool_calls = store.load_many(task_id, "tool_calls")
    require(
        any(
            item.get("tool_name") == "retrieve_source_chunks"
            and item.get("input", {}).get("algorithm") == "hybrid_rerank_v1"
            for item in tool_calls
        ),
        "Trace 没有记录实际 hybrid retrieval mode",
    )

    chunk_by_id = {item["id"]: item for item in store.load_many(task_id, "source_chunks")}
    page_by_id = {item.id: item for item in pages}
    selected_ids = set(run.selected_chunk_ids)
    allowed_source_ids = {item.id for item in sources}
    require(
        all(chunk_by_id[item]["task_id"] == task_id for item in selected_ids),
        "Hybrid retrieval 跨 task",
    )
    require(
        all(chunk_by_id[item]["source_id"] in allowed_source_ids for item in selected_ids),
        "Hybrid retrieval 跨未选 Source",
    )
    require(
        all(
            chunk_by_id[item]["content_hash"]
            == page_by_id[chunk_by_id[item]["web_page_id"]].content_hash
            for item in selected_ids
        ),
        "Hybrid retrieval 使用过期 content_hash",
    )
    for evidence in store.load_many(task_id, "evidence"):
        chunk_item = chunk_by_id[evidence["metadata"]["source_chunk_id"]]
        require(chunk_item["id"] in selected_ids, "Extractor 扫描了未选 chunk")
        page = page_by_id[evidence["metadata"]["web_page_id"]]
        require(page.content_hash == evidence["metadata"]["content_hash"], "content_hash provenance 断链")
        require(page.text[evidence["source_text_start"]:evidence["source_text_end"]] == evidence["snippet"], "absolute span/quote 断链")
        require(evidence["metadata"]["source_url"], "URL provenance 丢失")
    require(store.load_many(task_id, "evidence"), "Hybrid extractor 没产出证据")


def check_scope_failure_modes_and_backward_compatibility() -> None:
    real_dense = FastEmbedDenseBackend()
    real_reranker = FastEmbedRerankerBackend()
    require(
        real_dense.model_name == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        and real_reranker.model_name == "BAAI/bge-reranker-base",
        "FastEmbed production model IDs 错误",
    )
    require(
        real_dense.cache_dir == real_reranker.cache_dir == r"C:\agent-models"
        and real_dense.device == real_reranker.device == "cpu",
        "FastEmbed cache/device contract 错误",
    )
    for mode in ("off", "bm25_v1", "hybrid_v1", "hybrid_rerank_v1"):
        require(normalize_source_rag_mode(mode) == mode, f"mode switch 不支持 {mode}")
    try:
        normalize_source_rag_mode("invalid")
    except ValueError:
        pass
    else:
        raise AssertionError("非法 mode 没明确失败")

    old = SourceRetrievalRun(
        task_id="old_task",
        research_task_id="old_research",
        query_text="old query",
        query_hash="old_hash",
        selected_chunks=[{"chunk_id": "old_chunk", "rank": 1, "score": 1.0}],
    )
    require(old.retrieval_mode == "bm25_v1", "旧 Artifact 默认值不兼容")
    require(old.selected_chunks[0].dense_score is None, "旧 Hit 的缺失 dense score 不应变成 0")

    hybrid_task_id = "task_source_rag_hybrid_without_rerank"
    hybrid_research_id = "researchtask_source_rag_hybrid_without_rerank"
    hybrid_store = ArtifactStore(ROOT)
    hybrid_task, hybrid_need, hybrid_kiq = research_fixture(hybrid_task_id, hybrid_research_id)
    hybrid_source, hybrid_page = make_source_page(
        hybrid_task_id,
        hybrid_research_id,
        1,
        "星云IDE支持API和SDK集成，并提供代码协作与自动化工作流。" * 40,
    )
    save_base_artifacts(
        hybrid_store,
        hybrid_task_id,
        hybrid_task,
        hybrid_need,
        hybrid_kiq,
        [hybrid_source],
        [hybrid_page],
    )
    hybrid_run, hybrid_selected = SourceRAGService(
        store=hybrid_store,
        retrieval_mode="hybrid_v1",
        dense_backend=FakeDenseBackend(),
    ).run(
        task_id=hybrid_task_id,
        research_task=hybrid_task,
        source_ids=[hybrid_source.id],
    )
    require(hybrid_selected and hybrid_run.reranked_count == 0, "hybrid_v1 错误要求 reranker")
    require(all(item.rerank_score is None for item in hybrid_run.selected_chunks), "hybrid_v1 伪造 rerank score")

    task_id = "task_source_rag_hybrid_failure"
    research_task_id = "researchtask_source_rag_hybrid_failure"
    store = ArtifactStore(ROOT)
    research_task, need, kiq = research_fixture(task_id, research_task_id)
    source, page = make_source_page(
        task_id,
        research_task_id,
        1,
        "星云IDE支持API和SDK集成，并提供代码协作与自动化工作流。" * 30,
    )
    save_base_artifacts(store, task_id, research_task, need, kiq, [source], [page])
    try:
        SourceRAGService(
            store=store,
            retrieval_mode="hybrid_v1",
            dense_backend=FailingDenseBackend(),
        ).run(task_id=task_id, research_task=research_task, source_ids=[source.id])
    except RuntimeError as error:
        require("intentional dense failure" in str(error), "模型错误语义被吞掉")
    else:
        raise AssertionError("Dense 失败被静默伪装成功")
    require(not store.load_many(task_id, "source_retrieval_runs"), "失败仍写 completed retrieval run")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    check_dense_cache_and_semantic_recall()
    check_rrf_and_rerank_contract()
    check_final_selector_limits()
    check_hybrid_extractor_and_artifact()
    check_scope_failure_modes_and_backward_compatibility()
    print("check_source_rag_hybrid_rerank: PASS")
    print("bm25_baseline_covered_by=check_source_rag_r1.py")
    print("dense_cache_content_hash=true")
    print("rrf_k60_one_based_deterministic=true")
    print("dense_semantic_recall=true")
    print("reranker_top30_only=true")
    print("final_selector_10_3_20000=true")
    print("artifact_backward_compatible_no_vectors=true")
    print("hybrid_extractor_provenance=true")
    print("model_failure_is_explicit=true")
    print("external_calls=0")


if __name__ == "__main__":
    main()
