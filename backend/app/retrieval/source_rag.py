from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections import Counter
from collections.abc import Iterable

from app.harness.artifacts import ArtifactStore
from app.retrieval.hybrid import (
    DEFAULT_RRF_K,
    CrossEncoderReranker,
    DenseBackend,
    DenseRetriever,
    FinalSelector,
    ReciprocalRankFusion,
    RerankerBackend,
    RetrievalCandidate,
)
from app.schemas import (
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    SourceRetrievalHit,
    SourceRetrievalRun,
    WebPageContent,
)


SOURCE_RAG_MODES = {"off", "bm25_v1", "hybrid_v1", "hybrid_rerank_v1"}
DEFAULT_CHUNK_CHARS = 1500
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_TOP_K = 10
DEFAULT_MAX_CHUNKS_PER_SOURCE = 3
DEFAULT_SELECTED_TEXT_BUDGET = 20_000
_BOUNDARY_PATTERN = re.compile(r"\n+|[。！？!?；;]")
_LATIN_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_CHINESE_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u9fff]+")


def normalize_source_rag_mode(value: str | None = None) -> str:
    mode = (value if value is not None else os.getenv("SOURCE_RAG_MODE", "bm25_v1"))
    normalized = str(mode).strip().casefold() or "bm25_v1"
    if normalized not in SOURCE_RAG_MODES:
        raise ValueError(
            "SOURCE_RAG_MODE 只支持 off、bm25_v1、hybrid_v1 或 hybrid_rerank_v1，"
            f"当前值为：{normalized}"
        )
    return normalized


def _chunk_id(source_id: str, content_hash: str, start: int, end: int) -> str:
    fingerprint = f"{source_id}\n{content_hash}\n{start}\n{end}"
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
    return f"chunk_{digest}"


def _preferred_boundaries(text: str) -> list[int]:
    boundaries = {match.end() for match in _BOUNDARY_PATTERN.finditer(text)}
    boundaries.add(len(text))
    return sorted(boundaries)


def chunk_text_ranges(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> list[tuple[int, int]]:
    if target_chars < 200:
        raise ValueError("target_chars 必须至少为 200")
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("overlap_chars 必须大于等于 0 且小于 target_chars")
    if not text:
        return []

    boundaries = _preferred_boundaries(text)
    ranges: list[tuple[int, int]] = []
    start = 0
    text_length = len(text)
    minimum_chunk = max(target_chars // 2, 100)
    forward_slack = max(target_chars // 3, 100)

    while start < text_length:
        desired_end = min(start + target_chars, text_length)
        if desired_end == text_length:
            end = text_length
        else:
            lower = min(start + minimum_chunk, desired_end)
            before = [item for item in boundaries if lower <= item <= desired_end]
            if before:
                end = before[-1]
            else:
                after = [
                    item
                    for item in boundaries
                    if desired_end < item <= min(text_length, desired_end + forward_slack)
                ]
                end = after[0] if after else desired_end
        if end <= start:
            end = min(start + target_chars, text_length)
        ranges.append((start, end))
        if end >= text_length:
            break

        desired_start = max(start + 1, end - overlap_chars)
        nearby = [
            item
            for item in boundaries
            if desired_start <= item < end
            and item <= desired_start + max(overlap_chars // 2, 40)
        ]
        next_start = nearby[0] if nearby else desired_start
        start = next_start if next_start > start else end
    return ranges


def chunk_web_page(
    *,
    task_id: str,
    source: SourceDocument,
    page: WebPageContent,
    target_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> list[SourceChunk]:
    if source.task_id != task_id or page.task_id != task_id:
        raise ValueError("SourceChunk 不允许跨 task 生成")
    if page.source_id != source.id:
        raise ValueError("WebPageContent 与 SourceDocument 的 source_id 不一致")

    return [
        SourceChunk(
            id=_chunk_id(source.id, page.content_hash, start, end),
            task_id=task_id,
            source_id=source.id,
            web_page_id=page.id,
            content_hash=page.content_hash,
            chunk_index=index,
            source_text_start=start,
            source_text_end=end,
            text=page.text[start:end],
            title=page.title or source.title,
            competitor=source.competitor,
            origin_research_task_id=str(
                source.metadata.get("research_task_id") or ""
            ),
            metadata={
                "source_url": source.url,
                "chunker": "source_chunker_v1",
                "target_chars": target_chars,
                "overlap_chars": overlap_chars,
            },
        )
        for index, (start, end) in enumerate(
            chunk_text_ranges(
                page.text,
                target_chars=target_chars,
                overlap_chars=overlap_chars,
            )
        )
    ]


def tokenize_sparse(text: str) -> list[str]:
    normalized = str(text or "").casefold()
    tokens = [match.group(0) for match in _LATIN_TOKEN_PATTERN.finditer(normalized)]
    for match in _CHINESE_SEQUENCE_PATTERN.finditer(normalized):
        sequence = match.group(0)
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(
                sequence[index:index + 2]
                for index in range(len(sequence) - 1)
            )
    return tokens


def build_retrieval_query(
    research_task: ResearchTask,
    information_needs: Iterable[InformationNeed],
    kiqs: Iterable[KeyIntelligenceQuestion],
) -> str:
    need_by_id = {item.id: item for item in information_needs}
    kiq_by_id = {item.id: item for item in kiqs}
    need = need_by_id.get(research_task.information_need_id)
    kiq = kiq_by_id.get(need.question_id) if need is not None else None
    values = [
        research_task.objective,
        research_task.competitor,
        research_task.dimension,
        *research_task.query_hints,
        *(need.required_facts if need is not None else []),
        need.comparability_basis if need is not None else "",
        kiq.question if kiq is not None else "",
    ]
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            parts.append(cleaned)
    return "\n".join(parts)


def _bm25_scores(
    query_text: str,
    chunks: list[SourceChunk],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[SourceChunk, float, list[str]]]:
    if not chunks:
        return []
    query_terms = list(dict.fromkeys(tokenize_sparse(query_text)))
    document_tokens = [tokenize_sparse(item.text) for item in chunks]
    document_counts = [Counter(tokens) for tokens in document_tokens]
    document_frequency: Counter[str] = Counter()
    for tokens in document_tokens:
        document_frequency.update(set(tokens))
    average_length = sum(len(tokens) for tokens in document_tokens) / len(chunks)
    average_length = max(average_length, 1.0)
    total_documents = len(chunks)
    scored: list[tuple[SourceChunk, float, list[str]]] = []
    for chunk, tokens, counts in zip(chunks, document_tokens, document_counts):
        score = 0.0
        matched: list[str] = []
        document_length = len(tokens)
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency <= 0:
                continue
            matched.append(term)
            frequency_documents = document_frequency[term]
            inverse_frequency = math.log(
                1.0
                + (total_documents - frequency_documents + 0.5)
                / (frequency_documents + 0.5)
            )
            denominator = frequency + k1 * (
                1.0 - b + b * document_length / average_length
            )
            score += inverse_frequency * (frequency * (k1 + 1.0) / denominator)
        scored.append((chunk, score, matched[:20]))
    return scored


class SourceRAGService:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        target_chars: int = DEFAULT_CHUNK_CHARS,
        overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
        top_k: int = DEFAULT_TOP_K,
        max_chunks_per_source: int = DEFAULT_MAX_CHUNKS_PER_SOURCE,
        selected_text_budget: int = DEFAULT_SELECTED_TEXT_BUDGET,
        retrieval_mode: str = "bm25_v1",
        dense_backend: DenseBackend | None = None,
        reranker_backend: RerankerBackend | None = None,
    ):
        self.store = store
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars
        self.top_k = top_k
        self.max_chunks_per_source = max_chunks_per_source
        self.selected_text_budget = selected_text_budget
        self.retrieval_mode = normalize_source_rag_mode(retrieval_mode)
        if self.retrieval_mode == "off":
            raise ValueError("SourceRAGService cannot run in off mode")
        self.dense_backend = dense_backend
        self.reranker_backend = reranker_backend

    def run(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        source_ids: list[str],
    ) -> tuple[SourceRetrievalRun, list[SourceChunk]]:
        allowed_source_ids = set(source_ids)
        if not allowed_source_ids:
            raise ValueError("Source RAG 要求 extract task 明确提供 source_ids")
        sources = [
            SourceDocument(**item)
            for item in self.store.load_many(task_id, "sources")
            if item.get("id") in allowed_source_ids
        ]
        source_by_id = {item.id: item for item in sources}
        if set(source_by_id) != allowed_source_ids:
            missing = sorted(allowed_source_ids - set(source_by_id))
            raise ValueError(f"Source RAG 引用了不存在的 source_ids：{missing}")
        loaded_pages = [
            WebPageContent(**item)
            for item in self.store.load_many(task_id, "web_pages")
            if item.get("source_id") in allowed_source_ids
        ]
        latest_page_by_source: dict[str, WebPageContent] = {}
        for page in loaded_pages:
            current = latest_page_by_source.get(page.source_id)
            if current is None or page.fetched_at > current.fetched_at:
                latest_page_by_source[page.source_id] = page
        pages = list(latest_page_by_source.values())
        if not pages:
            raise ValueError("Source RAG 没有可检索的 WebPageContent")

        generated: list[SourceChunk] = []
        for page in pages:
            generated.extend(
                chunk_web_page(
                    task_id=task_id,
                    source=source_by_id[page.source_id],
                    page=page,
                    target_chars=self.target_chars,
                    overlap_chars=self.overlap_chars,
                )
            )
        existing = [
            SourceChunk(**item)
            for item in self.store.load_many(task_id, "source_chunks")
            if item.get("source_id") not in allowed_source_ids
        ]
        generated_by_id = {item.id: item for item in generated}
        self.store.save_many(
            task_id,
            "source_chunks",
            existing + list(generated_by_id.values()),
        )

        current_page_hashes = {item.id: item.content_hash for item in pages}
        candidates = [
            item
            for item in generated_by_id.values()
            if item.task_id == task_id
            and item.source_id in allowed_source_ids
            and current_page_hashes.get(item.web_page_id) == item.content_hash
        ]
        needs = [
            InformationNeed(**item)
            for item in self.store.load_many(task_id, "research_information_needs")
        ]
        kiqs = [
            KeyIntelligenceQuestion(**item)
            for item in self.store.load_many(task_id, "research_kiqs")
        ]
        query_text = build_retrieval_query(research_task, needs, kiqs)
        retrieval_started = time.perf_counter()
        bm25_started = time.perf_counter()
        scored = _bm25_scores(query_text, candidates)
        fallback_used = bool(scored) and all(score <= 0.0 for _, score, _ in scored)
        fallback_reason = "bm25_all_zero" if fallback_used else ""
        if fallback_used:
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
        bm25_ranked = [
            RetrievalCandidate(
                chunk=chunk,
                bm25_score=score,
                bm25_rank=rank,
                matched_terms=tuple(matched_terms),
            )
            for rank, (chunk, score, matched_terms) in enumerate(scored, start=1)
        ]
        bm25_ms = (time.perf_counter() - bm25_started) * 1000.0

        dense_count = 0
        fusion_count = 0
        reranked_count = 0
        cache_hits = 0
        cache_misses = 0
        dense_ms = 0.0
        fusion_ms = 0.0
        rerank_ms = 0.0
        embedding_model = ""
        reranker_model = ""
        ranked_for_selection = bm25_ranked

        if self.retrieval_mode in {"hybrid_v1", "hybrid_rerank_v1"}:
            dense_retriever = DenseRetriever(self.dense_backend)
            embedding_model = dense_retriever.backend.model_name
            dense_started = time.perf_counter()
            dense_result = dense_retriever.retrieve(query_text, candidates, top_k=20)
            dense_ms = (time.perf_counter() - dense_started) * 1000.0
            dense_count = len(dense_result.candidates)
            cache_hits = dense_result.cache_hits
            cache_misses = dense_result.cache_misses

            fusion_started = time.perf_counter()
            ranked_for_selection = ReciprocalRankFusion(k=DEFAULT_RRF_K).fuse(
                bm25_ranked[:20],
                dense_result.candidates,
                top_k=30,
            )
            fusion_ms = (time.perf_counter() - fusion_started) * 1000.0
            fusion_count = len(ranked_for_selection)

        if self.retrieval_mode == "hybrid_rerank_v1":
            reranker = CrossEncoderReranker(self.reranker_backend)
            reranker_model = reranker.backend.model_name
            rerank_started = time.perf_counter()
            ranked_for_selection = reranker.rerank(
                query_text,
                ranked_for_selection[:30],
            )
            rerank_ms = (time.perf_counter() - rerank_started) * 1000.0
            reranked_count = len(ranked_for_selection)

        selected_candidates = FinalSelector(
            top_k=self.top_k,
            max_chunks_per_source=self.max_chunks_per_source,
            max_total_chars=self.selected_text_budget,
        ).select(ranked_for_selection)
        if candidates and not selected_candidates:
            raise ValueError("Source RAG candidates cannot satisfy final selection limits")
        selected = [item.chunk for item in selected_candidates]
        selected_chars = sum(len(item.text) for item in selected)
        hits = []
        for candidate in selected_candidates:
            legacy_score = (
                candidate.rerank_score
                if candidate.rerank_score is not None
                else candidate.rrf_score
                if candidate.rrf_score is not None
                else candidate.bm25_score
                if candidate.bm25_score is not None
                else 0.0
            )
            hits.append(
                SourceRetrievalHit(
                    chunk_id=candidate.chunk_id,
                    source_id=candidate.source_id,
                    rank=candidate.final_rank or len(hits) + 1,
                    score=round(max(float(legacy_score), 0.0), 8),
                    matched_terms=list(candidate.matched_terms),
                    bm25_rank=candidate.bm25_rank,
                    bm25_score=(
                        round(candidate.bm25_score, 8)
                        if candidate.bm25_score is not None
                        else None
                    ),
                    dense_rank=candidate.dense_rank,
                    dense_score=(
                        round(candidate.dense_score, 8)
                        if candidate.dense_score is not None
                        else None
                    ),
                    rrf_rank=candidate.rrf_rank,
                    rrf_score=(
                        round(candidate.rrf_score, 8)
                        if candidate.rrf_score is not None
                        else None
                    ),
                    rerank_rank=candidate.rerank_rank,
                    rerank_score=(
                        round(candidate.rerank_score, 8)
                        if candidate.rerank_score is not None
                        else None
                    ),
                    final_rank=candidate.final_rank,
                )
            )

        query_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest()
        total_ms = (time.perf_counter() - retrieval_started) * 1000.0
        run = SourceRetrievalRun(
            task_id=task_id,
            research_task_id=research_task.id,
            algorithm=self.retrieval_mode,
            retrieval_mode=self.retrieval_mode,
            query_text=query_text,
            query_hash=query_hash,
            candidate_chunk_count=len(candidates),
            selected_chunk_ids=[item.id for item in selected],
            selected_chunks=hits,
            top_k=self.top_k,
            selected_text_chars=selected_chars,
            total_candidate_chars=sum(len(item.text) for item in candidates),
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            embedding_model=embedding_model,
            reranker_model=reranker_model,
            bm25_candidate_count=(
                min(20, len(bm25_ranked))
                if self.retrieval_mode != "bm25_v1"
                else len(bm25_ranked)
            ),
            dense_candidate_count=dense_count,
            fusion_candidate_count=fusion_count,
            reranked_count=reranked_count,
            selected_count=len(selected),
            rrf_k=DEFAULT_RRF_K,
            embedding_cache_hits=cache_hits,
            embedding_cache_misses=cache_misses,
            bm25_ms=round(bm25_ms, 3),
            dense_ms=round(dense_ms, 3),
            fusion_ms=round(fusion_ms, 3),
            rerank_ms=round(rerank_ms, 3),
            total_ms=round(total_ms, 3),
            metadata={
                "source_ids": sorted(allowed_source_ids),
                "max_chunks_per_source": self.max_chunks_per_source,
                "selected_text_budget": self.selected_text_budget,
                "tokenizer": "latin_word_numeric_plus_chinese_bigram_v1",
                "dense_top_k": 20,
                "fusion_top_k": 30,
            },
        )
        self.store.append_many(task_id, "source_retrieval_runs", [run])
        return run, selected
