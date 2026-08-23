from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    ProductCard,
    SourceDocument,
    SourceEvidence,
)
from build_product_cards_demo import DEFAULT_TASK_ID


REPORT_ID = "report_online_education_product_rd"
REPORT_TITLE = "面向产品研发的在线课堂实时互动解决方案竞品分析报告"
CREATED_BY_AGENT_RUN_ID = "rule_milestone3_report_builder"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic CompetitiveReport artifact from local "
            "ProductCard, AnalysisClaim, CitationCheck, SourceDocument, and "
            "SourceEvidence artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_artifacts(
    store: ArtifactStore,
    task_id: str,
) -> tuple[
    list[ProductCard],
    list[AnalysisClaim],
    list[CitationCheck],
    list[SourceDocument],
    list[SourceEvidence],
]:
    product_cards = [
        ProductCard(**item)
        for item in store.load_many(task_id, "product_cards")
    ]
    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]
    citation_checks = [
        CitationCheck(**item)
        for item in store.load_many(task_id, "citation_checks")
    ]
    sources = [SourceDocument(**item) for item in store.load_many(task_id, "sources")]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]

    if not product_cards:
        raise ValueError(
            f"No product_cards found for task_id={task_id}; run build_product_cards_demo.py first"
        )
    if not claims:
        raise ValueError(
            f"No claims found for task_id={task_id}; run build_claims_demo.py first"
        )
    if not citation_checks:
        raise ValueError(
            f"No citation_checks found for task_id={task_id}; run run_citation_check_demo.py first"
        )
    if not sources:
        raise ValueError(f"No sources found for task_id={task_id}")
    if not evidence:
        raise ValueError(f"No evidence found for task_id={task_id}")

    validate_claim_citation_links(claims, citation_checks)
    validate_product_card_links(product_cards, sources, evidence)
    return product_cards, claims, citation_checks, sources, evidence


def validate_claim_citation_links(
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
) -> None:
    claim_ids = {claim.id for claim in claims}
    checked_claim_ids = {check.claim_id for check in citation_checks}
    missing_checks = sorted(claim_ids - checked_claim_ids)
    if missing_checks:
        raise ValueError(
            "Claims missing citation checks: " + ", ".join(missing_checks)
        )


def validate_product_card_links(
    product_cards: list[ProductCard],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> None:
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    for card in product_cards:
        missing_sources = sorted(set(card.source_ids) - source_ids)
        missing_evidence = sorted(set(card.evidence_ids) - evidence_ids)
        if missing_sources:
            raise ValueError(
                f"{card.id} references missing source_ids: "
                + ", ".join(missing_sources)
            )
        if missing_evidence:
            raise ValueError(
                f"{card.id} references missing evidence_ids: "
                + ", ".join(missing_evidence)
            )


def build_report(
    task_id: str,
    product_cards: list[ProductCard],
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> CompetitiveReport:
    claims_by_dimension = {claim.dimension: claim for claim in claims}
    checks_by_claim_id = {check.claim_id: check for check in citation_checks}
    claim_ids = [claim.id for claim in claims]

    markdown = render_markdown(
        product_cards=product_cards,
        claims_by_dimension=claims_by_dimension,
        claims=claims,
        checks_by_claim_id=checks_by_claim_id,
        sources=sources,
        evidence=evidence,
    )
    validate_report_markdown(markdown, claim_ids)

    return CompetitiveReport(
        id=REPORT_ID,
        task_id=task_id,
        title=REPORT_TITLE,
        markdown=markdown,
        claim_ids=claim_ids,
        created_by_agent_run_id=CREATED_BY_AGENT_RUN_ID,
        sections={
            "task_background": True,
            "product_route_overview": [card.id for card in product_cards],
            "claim_dimensions": {
                claim.dimension: claim.id
                for claim in claims
            },
            "citation_status": {
                check.claim_id: check.status
                for check in citation_checks
            },
        },
        metadata={
            "builder": "build_report_demo.py",
            "rule": "deterministic_report_from_product_cards_claims_and_citations",
        },
    )


def render_markdown(
    *,
    product_cards: list[ProductCard],
    claims_by_dimension: dict[str, AnalysisClaim],
    claims: list[AnalysisClaim],
    checks_by_claim_id: dict[str, CitationCheck],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> str:
    positioning = claims_by_dimension["positioning"]
    feature = claims_by_dimension["feature"]
    pricing = claims_by_dimension["pricing"]
    ecosystem = claims_by_dimension["ecosystem"]
    risk = claims_by_dimension["risk"]

    lines: list[str] = [
        f"# {REPORT_TITLE}",
        "",
        "## 1. 任务背景",
        (
            "本报告面向产品研发团队，基于本地 Snapshot Mode 中已校验的 "
            "SourceDocument、SourceEvidence、ProductCard、AnalysisClaim 和 "
            "CitationCheck 生成。目标不是给出采购排名，而是沉淀可追溯的功能基线、"
            "技术接入、商业模式、风险与研发建议。"
        ),
        (
            "当前样本覆盖 ClassIn、腾讯云实时互动 / TRTC 教育方案、BigBlueButton "
            f"三类路线，产品定位差异已经由结构化 claim 支撑。 [{positioning.id}]"
        ),
        "",
        "## 2. 竞品产品路线概览",
    ]

    for card in sorted(product_cards, key=lambda item: item.name):
        lines.extend(
            [
                f"### {card.name}",
                f"- 公司/项目：{card.company or '未填写'}",
                f"- 定位：{card.positioning or '当前证据未覆盖'}",
                f"- 目标用户/场景：{format_list(card.target_users)}",
                f"- 核心功能：{format_list(card.core_features)}",
                f"- 证据覆盖：sources={len(card.source_ids)}，evidence={len(card.evidence_ids)}",
            ]
        )

    lines.extend(
        [
            (
                "总体看，ClassIn 更接近完整在线教学产品路线，腾讯云 LCIC 更接近云服务和"
                "低代码集成路线，BigBlueButton 更接近开源自部署虚拟教室路线。"
                f" [{positioning.id}]"
            ),
            "",
            "## 3. 功能基线观察",
            (
                "三家方案都覆盖在线课堂实时互动的基本能力，包括音视频互动、白板或共享教学"
                f"界面、课件/文档承载以及课堂协作工具。 [{feature.id}]"
            ),
            (
                "产品研发上，这意味着自研方案的功能基线不应只做会议音视频，还需要同时覆盖"
                "课件同步、互动白板、课堂控制、录制或协作记录等教学闭环能力。"
                f" [{feature.id}]"
            ),
            "",
            "## 4. 技术接入与生态分析",
            (
                "技术接入路线存在明显分化：ClassIn 体现为客户端、AI LMS 和硬件生态；"
                "腾讯云强调 Open API 与多端 SDK/客户端接入；BigBlueButton 的生态线索"
                f"集中在开源自部署和 LMS 集成。 [{ecosystem.id}]"
            ),
            (
                "研发建议优先把 API 边界、课件系统、排课/用户系统、LMS 对接点抽象清楚，"
                f"避免把课堂能力直接绑定到单一前端或单一云厂商接口。 [{ecosystem.id}]"
            ),
            "",
            "## 5. 商业模式与成本结构",
            (
                "商业模式与成本结构也不同：ClassIn 采用版本化年费套餐，腾讯云 LCIC "
                "体现为云套餐和用量额度，BigBlueButton 的当前证据则指向开源自部署，"
                f"成本重点从软件订阅转向服务器和运维。 [{pricing.id}]"
            ),
            (
                "面向产品研发评估时，应把显性套餐价格、API/高级功能门槛、音视频用量、"
                f"录制存储和自部署运维成本拆开建模。 [{pricing.id}]"
            ),
            "",
            "## 6. 风险与限制",
            (
                "主要风险集中在三类：开源自部署带来的运维责任，云服务/API 集成带来的供应商"
                "依赖，以及商业套餐对 API、容量、录制、互动路数等能力的分层限制。"
                f" [{risk.id}]"
            ),
            (
                "其中 BigBlueButton 的部署风险已被 citation check 识别为有效证据链；"
                "BigBlueButton 的 LMS 集成证据来自第三方来源，因此相关生态结论需要保留"
                "弱引用标记。"
                f" [{risk.id}] [{ecosystem.id}]"
            ),
            "",
            "## 7. 产品研发建议",
            (
                "建议一：先定义在线课堂功能基线，包括实时音视频、白板、课件同步、录制、"
                f"课堂互动和协作记录，再决定 SaaS、PaaS 或开源自部署路线。 [{feature.id}]"
            ),
            (
                "建议二：技术接入层应把课堂、课件、排课、用户、LMS、录制存储拆成稳定边界，"
                f"为后续 API 化和多端适配留出空间。 [{ecosystem.id}]"
            ),
            (
                "建议三：商业模式设计不要只比较标价，应同步估算用量、存储、API 权限、"
                f"运维和客户成功支持成本。 [{pricing.id}]"
            ),
            (
                "建议四：风险评审应区分自部署运维风险、云厂商锁定风险、套餐能力限制和"
                f"第三方弱证据风险。 [{risk.id}]"
            ),
            "",
            "## 8. Claim 引用列表",
        ]
    )

    evidence_by_id = {item.id: item for item in evidence}
    source_by_id = {source.id: source for source in sources}
    for claim in claims:
        check = checks_by_claim_id[claim.id]
        lines.extend(
            [
                f"### {claim.id}",
                f"- 维度：{claim.dimension}",
                f"- 状态：{check.status}",
                f"- 结论：{claim.claim_text}",
                f"- Evidence：{', '.join(claim.evidence_ids)}",
                f"- Source：{format_claim_sources(claim, evidence_by_id, source_by_id)}",
            ]
        )

    return "\n".join(lines) + "\n"


def format_list(items: list[str]) -> str:
    if not items:
        return "当前证据未覆盖"
    return "；".join(items)


def format_claim_sources(
    claim: AnalysisClaim,
    evidence_by_id: dict[str, SourceEvidence],
    source_by_id: dict[str, SourceDocument],
) -> str:
    source_ids = []
    for evidence_id in claim.evidence_ids:
        source_id = evidence_by_id[evidence_id].source_id
        if source_id not in source_ids:
            source_ids.append(source_id)
    return "；".join(
        f"{source_id}({source_by_id[source_id].source_type})"
        for source_id in source_ids
    )


def validate_report_markdown(markdown: str, claim_ids: list[str]) -> None:
    required_sections = [
        "## 1. 任务背景",
        "## 2. 竞品产品路线概览",
        "## 3. 功能基线观察",
        "## 4. 技术接入与生态分析",
        "## 5. 商业模式与成本结构",
        "## 6. 风险与限制",
        "## 7. 产品研发建议",
        "## 8. Claim 引用列表",
    ]
    missing_sections = [
        section for section in required_sections if section not in markdown
    ]
    if missing_sections:
        raise ValueError(
            "Report markdown missing required sections: "
            + ", ".join(missing_sections)
        )

    missing_claims = [
        claim_id for claim_id in claim_ids if f"[{claim_id}]" not in markdown
    ]
    if missing_claims:
        raise ValueError(
            "Report markdown missing claim references: "
            + ", ".join(missing_claims)
        )


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    product_cards, claims, citation_checks, sources, evidence = load_artifacts(
        store,
        args.task_id,
    )
    report = build_report(
        args.task_id,
        product_cards,
        claims,
        citation_checks,
        sources,
        evidence,
    )
    store.save_many(args.task_id, "reports", [report])

    print("CompetitiveReport demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print(f"reports=1 claim_ids={len(report.claim_ids)}")


if __name__ == "__main__":
    main()
