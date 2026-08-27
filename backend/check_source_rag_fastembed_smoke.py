from __future__ import annotations

import json

from app.retrieval.hybrid import FastEmbedDenseBackend, FastEmbedRerankerBackend


def dot(left, right) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def main() -> None:
    query = "Trae 提供哪些 AI 编程与自动化能力？"
    passages = [
        "Trae is an AI coding tool with agent-based software development features.",
        "这是一段与烹饪食谱有关的普通中文内容。",
    ]
    dense = FastEmbedDenseBackend()
    query_vectors = dense.encode_queries([query])
    passage_vectors = dense.encode_corpus(passages)
    if len(query_vectors) != 1 or len(passage_vectors) != 2:
        raise AssertionError("FastEmbed Dense output count mismatch")
    similarities = [dot(query_vectors[0], item) for item in passage_vectors]

    reranker = FastEmbedRerankerBackend()
    rerank_scores = [float(item) for item in reranker.compute_scores(query, passages)]
    if len(rerank_scores) != 2:
        raise AssertionError("FastEmbed reranker output count mismatch")

    print("check_source_rag_fastembed_smoke: PASS")
    print(
        json.dumps(
            {
                "dense_model": dense.model_name,
                "reranker_model": reranker.model_name,
                "cache_dir": dense.cache_dir,
                "device": dense.device,
                "query_shape": list(query_vectors[0].shape),
                "passage_shapes": [list(item.shape) for item in passage_vectors],
                "similarities": similarities,
                "rerank_scores": rerank_scores,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
