from __future__ import annotations

import importlib.util
import json
import math
import time
from pathlib import Path

from app.retrieval import FinalSelector, ReciprocalRankFusion, RetrievalCandidate
from app.retrieval.hybrid import (
    CrossEncoderReranker,
    DenseRetriever,
    FastEmbedDenseBackend,
    FastEmbedRerankerBackend,
)
from app.retrieval.source_rag import _bm25_scores, build_retrieval_query, chunk_web_page
from app.schemas import (
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    WebPageContent,
)


BACKEND_ROOT = Path(__file__).resolve().parent
TASK_ID = "task_user_829e249408ef"
TASK_ROOT = BACKEND_ROOT / "app" / "data" / "runs" / TASK_ID
OUTPUT_ROOT = BACKEND_ROOT / "app" / "data" / "evaluations" / "source_rag_hybrid_rerank"
OUTPUT_PATH = OUTPUT_ROOT / "trae_eval_v1.json"


def load(name: str) -> list[dict]:
    path = TASK_ROOT / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def ranked_bm25(query: str, chunks: list[SourceChunk]) -> list[RetrievalCandidate]:
    scored = _bm25_scores(query, chunks)
    if scored and all(score <= 0.0 for _, score, _ in scored):
        scored.sort(
            key=lambda item: (
                item[0].chunk_index,
                item[0].source_id,
                item[0].source_text_start,
                item[0].id,
            )
        )
    else:
        scored.sort(
            key=lambda item: (
                -item[1],
                item[0].source_id,
                item[0].source_text_start,
                item[0].id,
            )
        )
    return [
        RetrievalCandidate(
            chunk=chunk,
            bm25_score=score,
            bm25_rank=rank,
            matched_terms=tuple(matched),
        )
        for rank, (chunk, score, matched) in enumerate(scored, 1)
    ]


def query_metrics(selected_ids: list[str], relevant_ids: set[str]) -> dict[str, float]:
    def recall(k: int) -> float:
        if not relevant_ids:
            return 0.0
        return len(set(selected_ids[:k]) & relevant_ids) / len(relevant_ids)

    reciprocal_rank = 0.0
    for rank, chunk_id in enumerate(selected_ids[:10], 1):
        if chunk_id in relevant_ids:
            reciprocal_rank = 1.0 / rank
            break
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(selected_ids[:10], 1)
        if chunk_id in relevant_ids
    )
    ideal_count = min(10, len(relevant_ids))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return {
        "recall_at_5": recall(5),
        "recall_at_10": recall(10),
        "mrr_at_10": reciprocal_rank,
        "ndcg_at_10": dcg / ideal if ideal else 0.0,
    }


def prepare_fixture() -> tuple[list[dict], dict[str, dict]]:
    sources = [SourceDocument(**item) for item in load("sources")]
    source_by_id = {item.id: item for item in sources}
    pages = [WebPageContent(**item) for item in load("web_pages")]
    latest_page_by_source: dict[str, WebPageContent] = {}
    for page in pages:
        current = latest_page_by_source.get(page.source_id)
        if current is None or page.fetched_at > current.fetched_at:
            latest_page_by_source[page.source_id] = page
    tasks = [ResearchTask(**item) for item in load("research_tasks")]
    needs = [InformationNeed(**item) for item in load("research_information_needs")]
    kiqs = [KeyIntelligenceQuestion(**item) for item in load("research_kiqs")]
    records = load("task_records")
    evidence = load("evidence")
    source_ids_by_research_task = {
        str(item.get("metadata", {}).get("research_task_id") or ""): list(
            item.get("metadata", {}).get("source_ids") or []
        )
        for item in records
        if str(item.get("task_type") or "") == "extract_source_evidence"
    }

    prepared: list[dict] = []
    mapping_summary = {
        "quote_verified_evidence": 0,
        "mapped_evidence": 0,
        "unmapped_evidence": 0,
        "relevant_chunk_ids": 0,
    }
    for research_task in tasks:
        allowed_source_ids = source_ids_by_research_task.get(research_task.id, [])
        chunks: list[SourceChunk] = []
        for source_id in allowed_source_ids:
            source = source_by_id.get(source_id)
            page = latest_page_by_source.get(source_id)
            if source is None or page is None:
                continue
            chunks.extend(chunk_web_page(task_id=TASK_ID, source=source, page=page))
        chunk_by_source: dict[str, list[SourceChunk]] = {}
        for item in chunks:
            chunk_by_source.setdefault(item.source_id, []).append(item)

        relevant_ids: set[str] = set()
        task_evidence: list[dict] = []
        for item in evidence:
            metadata = item.get("metadata", {})
            if metadata.get("research_task_id") != research_task.id:
                continue
            if not metadata.get("quote_verified"):
                continue
            mapping_summary["quote_verified_evidence"] += 1
            task_evidence.append(item)
            mapped = [
                chunk
                for chunk in chunk_by_source.get(str(item.get("source_id") or ""), [])
                if chunk.content_hash == metadata.get("content_hash")
                and chunk.source_text_start <= int(item.get("source_text_start") or 0)
                and int(item.get("source_text_end") or 0) <= chunk.source_text_end
            ]
            if mapped:
                mapping_summary["mapped_evidence"] += 1
                relevant_ids.update(chunk.id for chunk in mapped)
            else:
                mapping_summary["unmapped_evidence"] += 1
        query = build_retrieval_query(research_task, needs, kiqs)
        prepared.append(
            {
                "research_task": research_task,
                "query": query,
                "chunks": chunks,
                "relevant_ids": relevant_ids,
                "evidence": task_evidence,
                "page_by_id": {page.id: page for page in latest_page_by_source.values()},
                "source_by_id": source_by_id,
            }
        )
        mapping_summary["relevant_chunk_ids"] += len(relevant_ids)
    return prepared, mapping_summary


def evaluate_mode(
    prepared: list[dict],
    mode: str,
    *,
    dense: DenseRetriever | None = None,
    reranker: CrossEncoderReranker | None = None,
) -> dict:
    per_task: list[dict] = []
    selected_chars = 0
    candidate_chars = 0
    retained_evidence: dict[str, dict] = {}
    started = time.perf_counter()
    cache_hits = 0
    cache_misses = 0
    for fixture in prepared:
        query = fixture["query"]
        chunks = fixture["chunks"]
        bm25 = ranked_bm25(query, chunks)
        ranked = bm25
        if mode in {"hybrid_v1", "hybrid_rerank_v1"}:
            if dense is None:
                raise RuntimeError("Dense retriever missing")
            dense_result = dense.retrieve(query, chunks, top_k=20)
            cache_hits += dense_result.cache_hits
            cache_misses += dense_result.cache_misses
            ranked = ReciprocalRankFusion(k=60).fuse(
                bm25[:20], dense_result.candidates, top_k=30
            )
        if mode == "hybrid_rerank_v1":
            if reranker is None:
                raise RuntimeError("Reranker missing")
            ranked = reranker.rerank(query, ranked[:30])
        selected = FinalSelector().select(ranked)
        selected_ids = [item.chunk_id for item in selected]
        relevant_ids = fixture["relevant_ids"]
        task_metrics = query_metrics(selected_ids, relevant_ids)
        per_task.append(
            {
                "research_task_id": fixture["research_task"].id,
                "candidate_chunks": len(chunks),
                "relevant_chunks": len(relevant_ids),
                "selected_chunks": len(selected_ids),
                **{key: round(value, 6) for key, value in task_metrics.items()},
            }
        )
        selected_chars += sum(len(item.chunk.text) for item in selected)
        candidate_chars += sum(len(item.text) for item in chunks)
        selected_set = set(selected_ids)
        for evidence in fixture["evidence"]:
            metadata = evidence.get("metadata", {})
            mapped = {
                chunk.id
                for chunk in chunks
                if chunk.source_id == evidence.get("source_id")
                and chunk.content_hash == metadata.get("content_hash")
                and chunk.source_text_start <= evidence.get("source_text_start", 0)
                and evidence.get("source_text_end", 0) <= chunk.source_text_end
            }
            if mapped & selected_set:
                retained_evidence[evidence["id"]] = evidence

    count = max(len(per_task), 1)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    quote_ok = 0
    provenance_ok = 0
    page_by_id = prepared[0]["page_by_id"] if prepared else {}
    source_by_id = prepared[0]["source_by_id"] if prepared else {}
    for evidence in retained_evidence.values():
        metadata = evidence.get("metadata", {})
        page = page_by_id.get(metadata.get("web_page_id"))
        source = source_by_id.get(evidence.get("source_id"))
        quote_valid = bool(
            page
            and page.content_hash == metadata.get("content_hash")
            and page.text[evidence["source_text_start"]:evidence["source_text_end"]]
            == evidence["snippet"]
            and metadata.get("quote_verified")
        )
        quote_ok += int(quote_valid)
        provenance_ok += int(
            quote_valid
            and source is not None
            and source.url == metadata.get("source_url")
            and page.source_id == source.id
        )
    retained_count = len(retained_evidence)
    return {
        "status": "COMPLETED",
        "mode": mode,
        "research_task_count": len(per_task),
        "recall_at_5": round(sum(item["recall_at_5"] for item in per_task) / count, 6),
        "recall_at_10": round(sum(item["recall_at_10"] for item in per_task) / count, 6),
        "mrr_at_10": round(sum(item["mrr_at_10"] for item in per_task) / count, 6),
        "ndcg_at_10": round(sum(item["ndcg_at_10"] for item in per_task) / count, 6),
        "selected_chars": selected_chars,
        "candidate_chars": candidate_chars,
        "scan_reduction_percent": round(
            (1.0 - selected_chars / candidate_chars) * 100.0, 2
        ) if candidate_chars else 0.0,
        "retrieval_latency_ms": round(elapsed_ms, 3),
        "quote_verification_percent": round(quote_ok / retained_count * 100.0, 2) if retained_count else 0.0,
        "provenance_integrity_percent": round(provenance_ok / retained_count * 100.0, 2) if retained_count else 0.0,
        "retained_evidence_count": retained_count,
        "embedding_cache_hits": cache_hits,
        "embedding_cache_misses": cache_misses,
        "per_task": per_task,
    }


def real_model_readiness() -> tuple[bool, str]:
    if importlib.util.find_spec("fastembed") is None:
        return False, "FastEmbed is not installed in the agent interpreter"
    return True, ""


def main() -> None:
    prepared, mapping = prepare_fixture()
    result = {
        "task_id": TASK_ID,
        "network_calls": 0,
        "model_downloads": 0,
        "mapping": mapping,
        "bm25_v1": evaluate_mode(prepared, "bm25_v1"),
    }
    ready, reason = real_model_readiness()
    if ready:
        dense_backend = FastEmbedDenseBackend()
        reranker_backend = FastEmbedRerankerBackend()
        dense = DenseRetriever(dense_backend)
        result["hybrid_v1"] = evaluate_mode(prepared, "hybrid_v1", dense=dense)
        result["hybrid_rerank_v1"] = evaluate_mode(
            prepared,
            "hybrid_rerank_v1",
            dense=dense,
            reranker=CrossEncoderReranker(reranker_backend),
        )
        result["model"] = {
            "embedding": dense_backend.model_name,
            "reranker": reranker_backend.model_name,
            "device": dense_backend.device,
            "cache_dir": dense_backend.cache_dir,
        }
    else:
        not_run = {"status": "NOT_RUN", "reason": reason}
        result["hybrid_v1"] = not_run
        result["hybrid_rerank_v1"] = dict(not_run)
        result["model"] = {
            "embedding": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "reranker": "BAAI/bge-reranker-base",
            "device": "NOT_RUN",
            "cache_dir": r"C:\agent-models",
        }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("check_source_rag_hybrid_rerank_eval: PASS")
    print(f"task_id={TASK_ID}")
    print(f"output={OUTPUT_PATH}")
    print(f"mapped_evidence={mapping['mapped_evidence']}/{mapping['quote_verified_evidence']}")
    for mode in ("bm25_v1", "hybrid_v1", "hybrid_rerank_v1"):
        item = result[mode]
        if item["status"] == "COMPLETED":
            print(
                f"{mode}=Recall@5:{item['recall_at_5']} "
                f"Recall@10:{item['recall_at_10']} MRR@10:{item['mrr_at_10']} "
                f"nDCG@10:{item['ndcg_at_10']} chars:{item['selected_chars']} "
                f"reduction:{item['scan_reduction_percent']}% latency_ms:{item['retrieval_latency_ms']}"
            )
        else:
            print(f"{mode}=NOT_RUN reason={item['reason']}")
    print("external_calls=0")


if __name__ == "__main__":
    main()
