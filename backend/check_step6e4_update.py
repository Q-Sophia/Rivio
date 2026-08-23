from __future__ import annotations

from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import refresh_step6e4_artifacts

TASK_ID = "task_step6e3_zhipu_tencent_meeting_pilot"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    store = ArtifactStore()
    result = refresh_step6e4_artifacts(TASK_ID, store=store)
    require(result["product_cards_count"] > 0, "ProductCard 未生成")
    require(result["evidence_coverage_count"] > 0, "EvidenceCoverage 未生成")
    product_cards = store.load_many(TASK_ID, "product_cards")
    coverage = store.load_many(TASK_ID, "evidence_coverage")
    require(len(product_cards) >= 1, "缺少 ProductCard 产物")
    require(len(coverage) >= 1, "缺少 EvidenceCoverage 产物")
    require(any(item["status"] in {"sufficient", "partial", "weak", "conflicting"} for item in coverage), "覆盖状态未写入")
    print("check_step6e4_update: PASS")
    print(f"product_cards={result['product_cards_count']}")
    print(f"evidence_coverage={result['evidence_coverage_count']}")
    print(f"research_gaps={result['research_gap_count']}")
    print(f"supplement_tasks={result['new_research_task_count']}")


if __name__ == "__main__":
    main()
