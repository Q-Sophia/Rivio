from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from threading import RLock
from typing import Protocol

from app.schemas import SourceChunk


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"
DEFAULT_FASTEMBED_CACHE_DIR = r"C:\agent-models"
DEFAULT_RRF_K = 60


class DenseBackend(Protocol):
    model_name: str

    def encode_queries(self, texts: list[str]) -> Sequence[Sequence[float]]: ...

    def encode_corpus(self, texts: list[str]) -> Sequence[Sequence[float]]: ...


class RerankerBackend(Protocol):
    model_name: str

    def compute_scores(self, query: str, passages: list[str]) -> Sequence[float]: ...


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk: SourceChunk
    bm25_score: float | None = None
    bm25_rank: int | None = None
    dense_score: float | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    rrf_rank: int | None = None
    rerank_score: float | None = None
    rerank_rank: int | None = None
    final_rank: int | None = None
    matched_terms: tuple[str, ...] = ()

    @property
    def chunk_id(self) -> str:
        return self.chunk.id

    @property
    def source_id(self) -> str:
        return self.chunk.source_id


@dataclass(frozen=True)
class DenseRetrievalResult:
    candidates: list[RetrievalCandidate]
    cache_hits: int
    cache_misses: int


_DENSE_VECTOR_CACHE: dict[tuple[str, str, str], tuple[float, ...]] = {}
_DENSE_VECTOR_CACHE_LOCK = RLock()


def clear_dense_embedding_cache() -> None:
    """Test/operations hook; vectors are process-local and never persisted."""

    with _DENSE_VECTOR_CACHE_LOCK:
        _DENSE_VECTOR_CACHE.clear()


def _as_vector(value: Sequence[float]) -> tuple[float, ...]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    return tuple(float(item) for item in value)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Dense query/corpus embedding dimensions do not match")
    return sum(float(a) * float(b) for a, b in zip(left, right))


class FastEmbedDenseBackend:
    """Lazy, process-reused FastEmbed ONNX dense backend running on CPU."""

    _models: dict[tuple[str, str], object] = {}
    _lock = RLock()

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        cache_dir: str = DEFAULT_FASTEMBED_CACHE_DIR,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.device = "cpu"

    def _model(self):
        key = (self.model_name, self.cache_dir)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as error:
                    raise RuntimeError(
                        "hybrid Source RAG requires FastEmbed TextEmbedding"
                    ) from error
                model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=self.cache_dir,
                    cuda=False,
                    lazy_load=True,
                )
                self._models[key] = model
            return model

    def encode_queries(self, texts: list[str]) -> Sequence[Sequence[float]]:
        return list(self._model().query_embed(texts))

    def encode_corpus(self, texts: list[str]) -> Sequence[Sequence[float]]:
        return list(self._model().passage_embed(texts))


class FastEmbedRerankerBackend:
    """Lazy, process-reused FastEmbed ONNX CrossEncoder backend on CPU."""

    _models: dict[tuple[str, str], object] = {}
    _lock = RLock()

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        *,
        cache_dir: str = DEFAULT_FASTEMBED_CACHE_DIR,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.device = "cpu"

    def _model(self):
        key = (self.model_name, self.cache_dir)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder
                except ImportError as error:
                    raise RuntimeError(
                        "hybrid rerank Source RAG requires FastEmbed TextCrossEncoder"
                    ) from error
                model = TextCrossEncoder(
                    model_name=self.model_name,
                    cache_dir=self.cache_dir,
                    cuda=False,
                    lazy_load=True,
                )
                self._models[key] = model
            return model

    def compute_scores(self, query: str, passages: list[str]) -> Sequence[float]:
        if not passages:
            return []
        return list(self._model().rerank(query, passages))


class DenseRetriever:
    def __init__(self, backend: DenseBackend | None = None):
        self.backend = backend or FastEmbedDenseBackend()

    def retrieve(
        self,
        query: str,
        chunks: list[SourceChunk],
        *,
        top_k: int = 20,
    ) -> DenseRetrievalResult:
        if not chunks:
            return DenseRetrievalResult([], 0, 0)
        query_vectors = self.backend.encode_queries([query])
        if len(query_vectors) != 1:
            raise ValueError("Dense backend must return exactly one query embedding")
        query_vector = _as_vector(query_vectors[0])

        vectors: dict[str, tuple[float, ...]] = {}
        missing_chunks: list[SourceChunk] = []
        cache_hits = 0
        with _DENSE_VECTOR_CACHE_LOCK:
            for chunk in chunks:
                key = (self.backend.model_name, chunk.id, chunk.content_hash)
                vector = _DENSE_VECTOR_CACHE.get(key)
                if vector is None:
                    missing_chunks.append(chunk)
                else:
                    vectors[chunk.id] = vector
                    cache_hits += 1
        if missing_chunks:
            encoded = self.backend.encode_corpus([item.text for item in missing_chunks])
            if len(encoded) != len(missing_chunks):
                raise ValueError("Dense backend returned a different corpus embedding count")
            with _DENSE_VECTOR_CACHE_LOCK:
                for chunk, raw_vector in zip(missing_chunks, encoded):
                    vector = _as_vector(raw_vector)
                    key = (self.backend.model_name, chunk.id, chunk.content_hash)
                    _DENSE_VECTOR_CACHE[key] = vector
                    vectors[chunk.id] = vector

        ranked = sorted(
            (
                RetrievalCandidate(chunk=chunk, dense_score=_dot(query_vector, vectors[chunk.id]))
                for chunk in chunks
            ),
            key=lambda item: (-float(item.dense_score), item.chunk_id),
        )[:top_k]
        ranked = [replace(item, dense_rank=rank) for rank, item in enumerate(ranked, 1)]
        return DenseRetrievalResult(ranked, cache_hits, len(missing_chunks))


class ReciprocalRankFusion:
    """RRF by chunk_id using the standard 1 / (k + rank) formula.

    The small formula follows LlamaIndex fusion_retriever.py's documented RRF
    behavior; no LlamaIndex dependency or non-trivial source is copied.
    """

    def __init__(self, *, k: int = DEFAULT_RRF_K):
        if k <= 0:
            raise ValueError("RRF k must be positive")
        self.k = k

    def fuse(
        self,
        bm25: list[RetrievalCandidate],
        dense: list[RetrievalCandidate],
        *,
        top_k: int = 30,
    ) -> list[RetrievalCandidate]:
        combined: dict[str, RetrievalCandidate] = {}
        for candidate in bm25:
            combined[candidate.chunk_id] = candidate
        for candidate in dense:
            current = combined.get(candidate.chunk_id)
            combined[candidate.chunk_id] = (
                candidate
                if current is None
                else replace(
                    current,
                    dense_score=candidate.dense_score,
                    dense_rank=candidate.dense_rank,
                )
            )
        fused: list[RetrievalCandidate] = []
        for candidate in combined.values():
            score = 0.0
            if candidate.bm25_rank is not None:
                score += 1.0 / (self.k + candidate.bm25_rank)
            if candidate.dense_rank is not None:
                score += 1.0 / (self.k + candidate.dense_rank)
            fused.append(replace(candidate, rrf_score=score))
        fused.sort(key=lambda item: (-float(item.rrf_score), item.chunk_id))
        return [replace(item, rrf_rank=rank) for rank, item in enumerate(fused[:top_k], 1)]


class CrossEncoderReranker:
    def __init__(self, backend: RerankerBackend | None = None):
        self.backend = backend or FastEmbedRerankerBackend()

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        scores = self.backend.compute_scores(
            query,
            [item.chunk.text for item in candidates],
        )
        if len(scores) != len(candidates):
            raise ValueError("Reranker returned a different score count")
        ranked = [
            replace(candidate, rerank_score=float(score))
            for candidate, score in zip(candidates, scores)
        ]
        ranked.sort(key=lambda item: (-float(item.rerank_score), item.chunk_id))
        return [replace(item, rerank_rank=rank) for rank, item in enumerate(ranked, 1)]


class FinalSelector:
    def __init__(
        self,
        *,
        top_k: int = 10,
        max_chunks_per_source: int = 3,
        max_total_chars: int = 20_000,
    ):
        self.top_k = top_k
        self.max_chunks_per_source = max_chunks_per_source
        self.max_total_chars = max_total_chars

    def select(self, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        selected: list[RetrievalCandidate] = []
        source_counts: Counter[str] = Counter()
        selected_chars = 0
        for candidate in candidates:
            if len(selected) >= self.top_k:
                break
            if source_counts[candidate.source_id] >= self.max_chunks_per_source:
                continue
            chunk_chars = len(candidate.chunk.text)
            if selected_chars + chunk_chars > self.max_total_chars:
                continue
            selected.append(replace(candidate, final_rank=len(selected) + 1))
            source_counts[candidate.source_id] += 1
            selected_chars += chunk_chars
        return selected
