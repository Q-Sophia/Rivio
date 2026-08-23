from __future__ import annotations

from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm.structured import validate_portfolio_v2_refs
from app.schemas import (
    CompetitiveAnalysisPortfolioV2,
    ProductCard,
    SourceDocument,
    SourceEvidence,
)


BACKEND_DIR = Path(__file__).resolve().parent
RUN_ROOT = BACKEND_DIR / "app" / "data" / "runs"
AB_ROOT = BACKEND_DIR / "app" / "data" / "ab_tests"
MOCK_TASK_ID = "snapshot_step6c_professional_mock"
HISTORICAL_FAILED_TASK_ID = (
    "step6c_deepseek_v4_candidate_2_2_1_pilot_v2_candidate"
)
FILTERED_REAL_TASK_ID = "step6c_deepseek_v4_candidate_2_2_2_pilot_v2_candidate"


def validate_task(root: Path, task_id: str) -> CompetitiveAnalysisPortfolioV2:
    store = ArtifactStore(root_dir=root)
    portfolio = CompetitiveAnalysisPortfolioV2(
        **store.load_many(task_id, "analysis_portfolios")[-1]
    )
    sources = [
        SourceDocument(**item) for item in store.load_many(task_id, "sources")
    ]
    evidence = [
        SourceEvidence(**item) for item in store.load_many(task_id, "evidence")
    ]
    cards = [
        ProductCard(**item) for item in store.load_many(task_id, "product_cards")
    ]
    validate_portfolio_v2_refs(
        portfolio,
        known_source_ids={item.id for item in sources},
        known_evidence_ids={item.id for item in evidence},
        known_competitors={item.name for item in cards},
        evidence_competitors={item.id: item.competitor for item in evidence},
    )
    return portfolio


def main() -> None:
    validate_task(RUN_ROOT, MOCK_TASK_ID)

    rejected = False
    rejection = ""
    try:
        validate_task(AB_ROOT, HISTORICAL_FAILED_TASK_ID)
    except ValueError as exc:
        rejection = str(exc)
        rejected = "多竞品结论未逐一覆盖所有参与方证据" in rejection

    if not rejected:
        raise AssertionError(
            "历史 2.2.1 真实失败样本没有被逐竞品证据校验器拒绝: "
            + rejection
        )

    filtered = validate_task(AB_ROOT, FILTERED_REAL_TASK_ID)
    filter_metadata = filtered.metadata.get("claim_alignment_filter") or {}
    if filter_metadata.get("rejected_claim_ids") != ["claim_006"]:
        raise AssertionError("2.2.2 过滤产物缺少 claim_006 审计记录")
    if len(filtered.items) != 9:
        raise AssertionError("2.2.2 过滤后应保留 9 条合格结论")

    print("STEP6C_EVIDENCE_ALIGNMENT_CHECK_PASS")
    print("mock_portfolio=passed")
    print("historical_2_2_1_real_portfolio=rejected_as_expected")
    print("filtered_2_2_2_real_portfolio=passed_with_9_claims")
    print("rejected_claim_id=claim_006")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
