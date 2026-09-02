from __future__ import annotations

from typing import Any

from app.agents.web_evidence import normalize_dimension
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisTask,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    ResearchAgentRun,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchGap,
    ResearchTask,
    SourceDocument,
    SourceEvidence,
    TaskBoard,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from app.workflow.taskboard import TaskBoardStore
from build_product_cards_demo import build_product_cards, validate_source_evidence_links


def _slug(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
    return "_".join(part for part in normalized.split("_") if part).strip("_") or "item"


def build_evidence_coverage(
    task_id: str,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    *,
    required_competitors: list[str] | None = None,
    required_dimensions: list[str] | None = None,
) -> list[EvidenceCoverage]:
    validate_source_evidence_links(sources, evidence)
    source_by_id = {source.id: source for source in sources}
    competitor_names = sorted(
        {item for item in (required_competitors or []) if item}
        | {item.competitor for item in evidence if item.competitor}
        | {item.competitor for item in sources if item.competitor}
    )
    dimensions = sorted(
        {normalize_dimension(item) for item in (required_dimensions or []) if item}
        | {normalize_dimension(str(item.dimension)) for item in evidence}
    )
    items: list[EvidenceCoverage] = []

    for competitor in competitor_names:
        competitor_evidence = [item for item in evidence if item.competitor == competitor]
        for dimension in dimensions:
            matches = [
                item
                for item in competitor_evidence
                if normalize_dimension(str(item.dimension)) == dimension
            ]
            if not matches:
                items.append(
                    EvidenceCoverage(
                        id=f"coverage_{_slug(competitor)}_{_slug(dimension)}",
                        task_id=task_id,
                        competitor=competitor,
                        dimension=dimension,
                        status=EvidenceCoverageStatus.MISSING,
                        source_ids=[],
                        evidence_ids=[],
                        limitations="尚未获得该维度的可靠直接证据。",
                    )
                )
                continue

            source_types = {
                (
                    source_by_id[item.source_id].source_type.value
                    if hasattr(source_by_id[item.source_id].source_type, "value")
                    else str(source_by_id[item.source_id].source_type)
                )
                for item in matches
                if item.source_id in source_by_id
            }
            has_conflict = any(bool(item.metadata.get("conflicting")) for item in matches)
            only_weak = bool(matches) and (
                source_types <= {"social"} or all(item.confidence < 0.5 for item in matches)
            )

            if has_conflict:
                status = EvidenceCoverageStatus.CONFLICTING
                limitation = "当前证据存在口径、时间或数值冲突，需优先核实。"
            elif only_weak:
                status = EvidenceCoverageStatus.WEAK
                limitation = "当前只有较弱来源，不能支撑高置信度判断。"
            elif len(matches) >= 2:
                status = EvidenceCoverageStatus.SUFFICIENT
                limitation = "证据数量已形成基础覆盖，仍建议采用统一口径复核。"
            else:
                status = EvidenceCoverageStatus.PARTIAL
                limitation = "当前只有单条直接资料，覆盖仍不完整。"

            items.append(
                EvidenceCoverage(
                    id=f"coverage_{_slug(competitor)}_{_slug(dimension)}",
                    task_id=task_id,
                    competitor=competitor,
                    dimension=dimension,
                    status=status,
                    source_ids=sorted({item.source_id for item in matches}),
                    evidence_ids=[item.id for item in matches],
                    limitations=limitation,
                )
            )

    return items


def _required_coverage_scope(
    store: ArtifactStore,
    task_id: str,
) -> tuple[list[str], list[str]]:
    plans = [
        ResearchPlan(**item)
        for item in store.load_many(task_id, "research_plans")
    ]
    plan = plans[-1] if plans else None
    research_tasks = [
        ResearchTask(**item)
        for item in store.load_many(task_id, "research_tasks")
    ]
    task_items = [
        AnalysisTask(**item)
        for item in store.load_many(task_id, "analysis_tasks")
    ]
    analysis_task = task_items[-1] if task_items else None
    required_competitors = sorted(
        {item.competitor for item in research_tasks if item.competitor}
        | set(analysis_task.competitors if analysis_task else [])
        | set(
            plan.covered_competitors + plan.missing_competitors
            if plan
            else []
        )
    )
    required_dimensions = sorted(
        {item.dimension for item in research_tasks if item.dimension}
        | set(analysis_task.focus_areas if analysis_task else [])
        | set(
            plan.covered_dimensions + plan.missing_dimensions
            if plan
            else []
        )
    )
    return required_competitors, required_dimensions


class ResearchAgentCompatibilityProjectionService:
    """Project R1 verified evidence into legacy ProductCard/Coverage artifacts."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()

    def project(self, task_id: str) -> dict[str, Any]:
        verified_evidence_ids = {
            evidence_id
            for raw in self.store.load_many(task_id, "research_agent_runs")
            for evidence_id in ResearchAgentRun(**raw).verified_evidence_ids
        }
        if not verified_evidence_ids:
            self.store.save_many(task_id, "product_cards", [])
            self.store.save_many(task_id, "evidence_coverage", [])
            return {
                "task_id": task_id,
                "projected": False,
                "verified_evidence_count": 0,
                "product_cards_count": 0,
                "evidence_coverage_count": 0,
                "reason": "no_research_agent_verified_evidence_cleared_projection",
            }

        evidence = [
            SourceEvidence(**raw)
            for raw in self.store.load_many(task_id, "evidence")
            if str(raw.get("id") or "") in verified_evidence_ids
        ]
        found_evidence_ids = {item.id for item in evidence}
        missing_evidence_ids = sorted(verified_evidence_ids - found_evidence_ids)
        if missing_evidence_ids:
            raise ValueError(
                "Research Agent Run 引用了不存在的 Verified Evidence："
                + "、".join(missing_evidence_ids)
            )

        referenced_source_ids = {item.source_id for item in evidence}
        sources = [
            SourceDocument(**raw)
            for raw in self.store.load_many(task_id, "sources")
            if str(raw.get("id") or "") in referenced_source_ids
        ]
        validate_source_evidence_links(sources, evidence)

        cards = [
            card.model_copy(
                update={
                    "metadata": {
                        **card.metadata,
                        "source": "research_agent_r1_compat_projection",
                        "verified_evidence_only": True,
                    }
                }
            )
            for card in build_product_cards(task_id, sources, evidence)
        ]
        required_competitors, required_dimensions = _required_coverage_scope(
            self.store,
            task_id,
        )
        coverage = [
            item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "source": "research_agent_r1_compat_projection",
                        "verified_evidence_only": True,
                    }
                }
            )
            for item in build_evidence_coverage(
                task_id,
                sources,
                evidence,
                required_competitors=required_competitors,
                required_dimensions=required_dimensions,
            )
        ]
        self.store.save_many(task_id, "product_cards", cards)
        self.store.save_many(task_id, "evidence_coverage", coverage)
        return {
            "task_id": task_id,
            "projected": True,
            "verified_evidence_count": len(evidence),
            "product_cards_count": len(cards),
            "evidence_coverage_count": len(coverage),
            "reason": "research_agent_verified_evidence_projected",
        }


def build_research_gaps(
    task_id: str,
    coverage: list[EvidenceCoverage],
    *,
    default_source_types: list[str] | None = None,
) -> list[ResearchGap]:
    default_source_types = default_source_types or ["official_site", "docs", "pricing_page"]
    gaps: list[ResearchGap] = []
    for item in coverage:
        if item.status not in {
            EvidenceCoverageStatus.MISSING,
            EvidenceCoverageStatus.PARTIAL,
            EvidenceCoverageStatus.WEAK,
            EvidenceCoverageStatus.CONFLICTING,
        }:
            continue
        status_label = {
            EvidenceCoverageStatus.MISSING: "缺失",
            EvidenceCoverageStatus.PARTIAL: "部分覆盖",
            EvidenceCoverageStatus.WEAK: "较弱",
            EvidenceCoverageStatus.CONFLICTING: "冲突",
        }[item.status]
        gap = ResearchGap(
            id=f"gap_{_slug(item.competitor)}_{_slug(item.dimension)}_{status_label}",
            task_id=task_id,
            competitors=[item.competitor],
            dimension=item.dimension,
            missing_information=f"{item.competitor} 在 {item.dimension} 维度的证据处于{status_label}状态。",
            decision_blocked="该维度的可比口径或直接证据尚不足以支持稳定判断。",
            why_existing_evidence_is_insufficient=item.limitations,
            suggested_queries=[
                f"查找 {item.competitor} 在 {item.dimension} 维度的官方资料与同版本说明",
                f"补充 {item.competitor} 在 {item.dimension} 维度的对比数据或客观说明",
            ],
            preferred_source_types=default_source_types,
            priority=(TaskPriority.HIGH if item.status in {EvidenceCoverageStatus.CONFLICTING, EvidenceCoverageStatus.WEAK} else TaskPriority.MEDIUM),
            stop_condition="至少获得 1 条可追溯至官方或可靠第三方资料的直接证据，或确认该信息无法公开获取。",
            related_evidence_ids=item.evidence_ids,
            metadata={
                "source": "step6e4_coverage_gap_v1",
                "coverage_id": item.id,
                "coverage_status": item.status,
                "trigger_competitor": item.competitor,
                "trigger_dimension": item.dimension,
            },
        )
        gaps.append(gap)
    return gaps


def build_step6e4_research_tasks(
    task_id: str,
    gaps: list[ResearchGap],
    *,
    existing_tasks: list[ResearchTask] | None = None,
    max_collection_rounds: int = 3,
    total_source_budget_reached: bool = False,
) -> list[ResearchTask]:
    existing_tasks = existing_tasks or []
    tasks: list[ResearchTask] = []
    if total_source_budget_reached:
        return tasks

    # Collection rounds are global phases: round 1 is always the initial
    # ResearchTask set, so the first Coverage-driven supplement is round 2.
    current_round = max(
        (item.collection_round for item in existing_tasks),
        default=1,
    )
    next_round = current_round + 1
    if next_round > max_collection_rounds:
        return tasks

    gaps_by_scope: dict[tuple[str, str], list[ResearchGap]] = {}
    for gap in gaps:
        competitors = gap.competitors or [""]
        normalized_dimension = normalize_dimension(gap.dimension)
        for competitor in competitors:
            scope_key = (competitor, normalized_dimension)
            gaps_by_scope.setdefault(scope_key, []).append(gap)

    existing_scope_rounds = {
        (
            item.collection_round,
            item.competitor,
            normalize_dimension(item.dimension),
        )
        for item in existing_tasks
    }
    priority_order = {
        TaskPriority.HIGH.value: 0,
        TaskPriority.MEDIUM.value: 1,
        TaskPriority.LOW.value: 2,
    }

    for (competitor, normalized_dimension), scope_gaps in gaps_by_scope.items():
        matching = [
            item
            for item in existing_tasks
            if item.competitor == competitor
            and normalize_dimension(item.dimension) == normalized_dimension
        ]
        latest = max(matching, key=lambda item: item.collection_round) if matching else None
        # Coverage supplements continue a real planned InformationNeed. A gap
        # without an owning ResearchTask is not executable Mission work.
        if latest is None or not latest.information_need_id:
            continue
        if latest and latest.collection_round == current_round and latest.status == "waiting_for_collector":
            continue
        scope_round_key = (next_round, competitor, normalized_dimension)
        if scope_round_key in existing_scope_rounds:
            continue

        ordered_gaps = sorted(
            scope_gaps,
            key=lambda item: (
                priority_order.get(
                    item.priority.value
                    if hasattr(item.priority, "value")
                    else str(item.priority),
                    99,
                ),
                item.id,
            ),
        )
        primary_gap = ordered_gaps[0]
        gap_provenance = [
            {
                "research_gap_id": gap.id,
                "coverage_id": str(gap.metadata.get("coverage_id") or ""),
                "coverage_status": str(
                    gap.metadata.get("coverage_status") or ""
                ),
                "competitor": competitor,
                "dimension": normalized_dimension,
            }
            for gap in ordered_gaps
        ]
        priority_value = (
            primary_gap.priority.value
            if hasattr(primary_gap.priority, "value")
            else str(primary_gap.priority)
        )
        gap_id = (
            f"researchtask_{_slug(competitor)}_{_slug(normalized_dimension)}_"
            f"{_slug(priority_value)}_round_{next_round}"
        )
        tasks.append(
            ResearchTask(
                id=gap_id,
                task_id=task_id,
                information_need_id=latest.information_need_id,
                title=f"第 {next_round} 轮补采 {competitor} 的{normalized_dimension}信息",
                objective=f"补齐 {competitor} 在 {normalized_dimension} 维度的证据，解决当前覆盖缺口。",
                competitor=competitor,
                dimension=normalized_dimension,
                query_hints=[
                    f"{competitor} {normalized_dimension} 官方",
                    f"{competitor} {normalized_dimension} 文档 第{next_round}轮核验",
                ],
                seed_urls=[],
                preferred_domains=[],
                snapshot_source_ids=[],
                preferred_source_types=list(
                    dict.fromkeys(
                        source_type
                        for gap in ordered_gaps
                        for source_type in gap.preferred_source_types
                    )
                )
                or ["official_site", "docs"],
                priority=primary_gap.priority,
                status="waiting_for_collector",
                stop_condition=primary_gap.stop_condition,
                assigned_role=AgentRole.RESEARCHER,
                collection_round=next_round,
                parent_research_task_id=latest.id if latest else "",
                research_gap_id=primary_gap.id,
                metadata={
                    "source": "r1_bounded_coverage_supplement",
                    "trigger_competitor": competitor,
                    "trigger_dimension": normalized_dimension,
                    "trigger_coverage_id": str(
                        primary_gap.metadata.get("coverage_id") or ""
                    ),
                    "trigger_coverage_status": str(
                        primary_gap.metadata.get("coverage_status") or ""
                    ),
                    "trigger_gap_id": primary_gap.id,
                    "trigger_gap_ids": [gap.id for gap in ordered_gaps],
                    "trigger_coverage_ids": list(
                        dict.fromkeys(
                            item["coverage_id"]
                            for item in gap_provenance
                            if item["coverage_id"]
                        )
                    ),
                    "trigger_coverage_statuses": list(
                        dict.fromkeys(
                            item["coverage_status"]
                            for item in gap_provenance
                            if item["coverage_status"]
                        )
                    ),
                    "gap_provenance": gap_provenance,
                    "collection_round": next_round,
                },
            )
        )
        existing_scope_rounds.add(scope_round_key)
    return tasks


def _merge_unique_items(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], key_name: str = "id") -> list[dict[str, Any]]:
    by_key = {str(item.get(key_name, "")): item for item in existing}
    for item in incoming:
        key = str(item.get(key_name, ""))
        if key:
            by_key[key] = item
    return list(by_key.values())


def _refresh_bounded_gap_state(
    *,
    store: ArtifactStore,
    task_id: str,
    coverage: list[EvidenceCoverage],
    total_source_count: int,
    publish_research_tasks,
) -> dict[str, Any]:
    """Reuse the V1 Coverage -> Gap -> bounded ResearchTask state machine."""
    plans = [
        ResearchPlan(**item)
        for item in store.load_many(task_id, "research_plans")
    ]
    plan = plans[-1] if plans else None
    existing_tasks = [
        ResearchTask(**item)
        for item in store.load_many(task_id, "research_tasks")
    ]
    required_competitors, _required_dimensions = _required_coverage_scope(
        store,
        task_id,
    )

    existing_gaps = [
        ResearchGap(**item)
        for item in store.load_many(task_id, "research_gaps")
    ]
    new_gaps = build_research_gaps(task_id, coverage)
    merged_gaps = _merge_unique_items(
        [
            item.model_dump(mode="json")
            for item in existing_gaps
            if item.metadata.get("source") != "step6e4_coverage_gap_v1"
        ],
        [item.model_dump(mode="json") for item in new_gaps],
    )
    store.save_many(
        task_id,
        "research_gaps",
        [ResearchGap(**item) for item in merged_gaps],
    )

    max_rounds = plan.budget.max_collection_rounds if plan else 3
    max_total_sources = plan.budget.max_total_sources if plan else 40
    total_source_budget_reached = total_source_count >= max_total_sources
    current_step6e4_gaps = [
        ResearchGap(**item)
        for item in merged_gaps
        if item.get("metadata", {}).get("source")
        == "step6e4_coverage_gap_v1"
    ]
    new_tasks = build_step6e4_research_tasks(
        task_id,
        current_step6e4_gaps,
        existing_tasks=existing_tasks,
        max_collection_rounds=max_rounds,
        total_source_budget_reached=total_source_budget_reached,
    )
    publish_research_tasks(task_id, new_tasks)

    exhausted_gap_ids = []
    for gap in current_step6e4_gaps:
        matching_rounds = [
            item.collection_round
            for item in existing_tasks
            if item.competitor in gap.competitors
            and normalize_dimension(item.dimension)
            == normalize_dimension(gap.dimension)
        ]
        if total_source_budget_reached or (
            matching_rounds and max(matching_rounds) >= max_rounds
        ):
            exhausted_gap_ids.append(gap.id)

    if plan:
        sufficient_pairs = {
            (item.competitor, item.dimension)
            for item in coverage
            if item.status == EvidenceCoverageStatus.SUFFICIENT
        }
        normalized_dimensions = sorted({item.dimension for item in coverage})
        fully_covered_dimensions = [
            dimension
            for dimension in normalized_dimensions
            if required_competitors
            and all(
                (competitor, dimension) in sufficient_pairs
                for competitor in required_competitors
            )
        ]
        missing_dimensions = [
            item
            for item in normalized_dimensions
            if item not in fully_covered_dimensions
        ]
        plan = plan.model_copy(
            update={
                "status": (
                    ResearchPlanStatus.READY_FOR_ANALYSIS
                    if not current_step6e4_gaps
                    else ResearchPlanStatus.BLOCKED
                    if exhausted_gap_ids
                    and len(exhausted_gap_ids) == len(current_step6e4_gaps)
                    else ResearchPlanStatus.NEEDS_COLLECTION
                ),
                "research_task_ids": list(
                    dict.fromkeys(
                        [
                            *plan.research_task_ids,
                            *(item.id for item in new_tasks),
                        ]
                    )
                ),
                "covered_dimensions": fully_covered_dimensions,
                "missing_dimensions": missing_dimensions,
                "metadata": {
                    **plan.metadata,
                    "step6e4_last_refresh": utc_now().isoformat(),
                    "step6e4_exhausted_gap_ids": exhausted_gap_ids,
                },
            }
        )
        store.save_many(task_id, "research_plans", [*plans[:-1], plan])

    all_tasks = existing_tasks + new_tasks
    return {
        "research_gap_count": len(merged_gaps),
        "new_research_task_count": len(new_tasks),
        "new_research_task_ids": [item.id for item in new_tasks],
        "max_collection_rounds": max_rounds,
        "current_collection_round": max(
            [item.collection_round for item in all_tasks],
            default=0,
        ),
        "total_sources": total_source_count,
        "max_total_sources": max_total_sources,
        "source_budget_exhausted": total_source_budget_reached,
        "exhausted_gap_count": len(exhausted_gap_ids),
        "exhausted_gap_ids": exhausted_gap_ids,
        "coverage_status_counts": {
            item.value: sum(1 for entry in coverage if entry.status == item)
            for item in EvidenceCoverageStatus
        },
    }


class Step6E4RefreshService:
    """Refresh SourceEvidence -> ProductCard -> EvidenceCoverage and queue bounded gap tasks."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()

    def refresh(self, task_id: str) -> dict[str, Any]:
        sources = [SourceDocument(**item) for item in self.store.load_many(task_id, "sources")]
        evidence = [SourceEvidence(**item) for item in self.store.load_many(task_id, "evidence")]
        if not sources:
            raise ValueError(f"未找到 SourceDocument（来源）: {task_id}")
        if not evidence:
            raise ValueError(f"未找到 SourceEvidence（证据）: {task_id}")

        validate_source_evidence_links(sources, evidence)
        required_competitors, required_dimensions = _required_coverage_scope(
            self.store,
            task_id,
        )

        product_cards = build_product_cards(task_id, sources, evidence)
        coverage = build_evidence_coverage(
            task_id,
            sources,
            evidence,
            required_competitors=required_competitors,
            required_dimensions=required_dimensions,
        )
        self.store.save_many(task_id, "product_cards", product_cards)
        self.store.save_many(task_id, "evidence_coverage", coverage)

        gap_summary = _refresh_bounded_gap_state(
            store=self.store,
            task_id=task_id,
            coverage=coverage,
            total_source_count=len(sources),
            publish_research_tasks=self._publish_research_tasks,
        )
        return {
            "task_id": task_id,
            "product_cards_count": len(product_cards),
            "evidence_coverage_count": len(coverage),
            **gap_summary,
        }

    def _publish_research_tasks(self, task_id: str, tasks: list[ResearchTask]) -> None:
        if not tasks:
            return
        existing_tasks = [ResearchTask(**item) for item in self.store.load_many(task_id, "research_tasks")]
        combined_tasks = existing_tasks + [task for task in tasks if task.id not in {item.id for item in existing_tasks}]
        if combined_tasks:
            self.store.save_many(task_id, "research_tasks", combined_tasks)

        taskboard = TaskBoardStore(self.store).load_board(task_id) or TaskBoard(task_id=task_id, tasks=[])
        board_tasks = list(taskboard.tasks)
        for task in tasks:
            record = TaskRecord(
                id=f"queue_{task.id}",
                task_id=task_id,
                task_key=task.id,
                task_type=TaskType.SUPPLEMENT_COLLECTION,
                target_agent_role=AgentRole.RESEARCHER,
                status=TaskStatus.READY,
                priority=task.priority,
                reason=task.objective,
                input_refs=[
                    ref
                    for ref in [
                        *(
                            f"evidence_coverage:{coverage_id}"
                            for coverage_id in (
                                task.metadata.get("trigger_coverage_ids")
                                or [task.metadata.get("trigger_coverage_id")]
                            )
                            if coverage_id
                        ),
                        *(
                            f"research_gap:{gap_id}"
                            for gap_id in (
                                task.metadata.get("trigger_gap_ids")
                                or [task.research_gap_id]
                            )
                            if gap_id
                        ),
                    ]
                    if not ref.endswith(":")
                ],
                metadata={
                    "research_task_id": task.id,
                    "source": "R1 bounded coverage supplement",
                    "dimension": task.dimension,
                    "competitor": task.competitor,
                    "coverage_id": task.metadata.get("trigger_coverage_id", ""),
                    "coverage_status": task.metadata.get(
                        "trigger_coverage_status", ""
                    ),
                    "research_gap_id": task.research_gap_id,
                    "research_gap_ids": task.metadata.get(
                        "trigger_gap_ids",
                        [task.research_gap_id] if task.research_gap_id else [],
                    ),
                    "coverage_ids": task.metadata.get(
                        "trigger_coverage_ids",
                        [task.metadata.get("trigger_coverage_id")]
                        if task.metadata.get("trigger_coverage_id")
                        else [],
                    ),
                    "collection_round": task.collection_round,
                    "max_collection_rounds": (
                        max(
                            [
                                ResearchPlan(**item).budget.max_collection_rounds
                                for item in self.store.load_many(task_id, "research_plans")
                            ],
                            default=3,
                        )
                    ),
                },
            )
            board_tasks = [
                existing_record
                for existing_record in board_tasks
                if existing_record.task_key != task.id and existing_record.id != record.id
            ]
            board_tasks.append(record)

        taskboard.tasks = board_tasks
        taskboard.updated_at = utc_now()
        taskboard.status = TaskStatus.READY if any(item.status in {TaskStatus.READY, TaskStatus.CLAIMED, TaskStatus.RUNNING} for item in board_tasks) else TaskStatus.COMPLETED
        TaskBoardStore(self.store).save_board(taskboard)


class ResearchAgentBoundedRefreshService:
    """Recompute R1-only derived artifacts and publish bounded supplements."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()
        self.projection_service = ResearchAgentCompatibilityProjectionService(
            store=self.store
        )
        self.queue_service = Step6E4RefreshService(store=self.store)

    def refresh(self, task_id: str) -> dict[str, Any]:
        projection = self.projection_service.project(task_id)
        coverage = [
            EvidenceCoverage(**item)
            for item in self.store.load_many(task_id, "evidence_coverage")
        ]

        # The direct compatibility bridge intentionally emits no Coverage when
        # R1 has no verified evidence. Inside the bounded loop, however, the
        # required scope must still be represented as MISSING so it can trigger
        # a traceable supplement instead of silently ending after round one.
        if not coverage:
            required_competitors, required_dimensions = _required_coverage_scope(
                self.store,
                task_id,
            )
            coverage = [
                item.model_copy(
                    update={
                        "metadata": {
                            **item.metadata,
                            "source": "research_agent_r1_compat_projection",
                            "verified_evidence_only": True,
                        }
                    }
                )
                for item in build_evidence_coverage(
                    task_id,
                    [],
                    [],
                    required_competitors=required_competitors,
                    required_dimensions=required_dimensions,
                )
            ]
            self.store.save_many(task_id, "evidence_coverage", coverage)

        gap_summary = _refresh_bounded_gap_state(
            store=self.store,
            task_id=task_id,
            coverage=coverage,
            total_source_count=len(self.store.load_many(task_id, "sources")),
            publish_research_tasks=self.queue_service._publish_research_tasks,
        )
        return {
            "task_id": task_id,
            "product_cards_count": len(
                self.store.load_many(task_id, "product_cards")
            ),
            "evidence_coverage_count": len(coverage),
            "projection": projection,
            **gap_summary,
        }


class AnalystGapPublisherService:
    """Read known source evidence, find missing/weak/conflicting coverage, and publish research tasks."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()
        self.refresh_service = Step6E4RefreshService(store=self.store)

    def publish(self, task_id: str) -> dict[str, Any]:
        summary = self.refresh_service.refresh(task_id)
        return {
            **summary,
            "coverage_count": summary["evidence_coverage_count"],
            "gap_count": summary["research_gap_count"],
            "research_task_count": summary["new_research_task_count"],
            "status_counts": summary["coverage_status_counts"],
        }


class Step6E4QueueService:
    """Let Analyst（分析智能体）claim one coverage-evaluation task from TaskBoard."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()
        self.board_store = TaskBoardStore(self.store)
        self.refresh_service = Step6E4RefreshService(store=self.store)

    def run_once(self, task_id: str) -> dict[str, Any]:
        ready = [
            record
            for record in self.board_store.ready_records(task_id)
            if record.target_agent_role == AgentRole.ANALYST
            and record.task_type == TaskType.EVALUATE_EVIDENCE_COVERAGE
        ]
        if not ready:
            raise LookupError("没有可由 Analyst（分析智能体）领取的证据覆盖评估任务。")
        record = ready[0]
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.CLAIMED,
            claimed_by_agent="evidence_coverage_analyst_agent",
        )
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.RUNNING,
            claimed_by_agent="evidence_coverage_analyst_agent",
        )
        try:
            summary = self.refresh_service.refresh(task_id)
        except Exception as exc:
            self.board_store.update_status(
                task_id,
                record.task_key,
                TaskStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                claimed_by_agent="evidence_coverage_analyst_agent",
            )
            raise
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.COMPLETED,
            output_refs=["product_cards", "evidence_coverage", "research_gaps", "research_tasks"],
            claimed_by_agent="evidence_coverage_analyst_agent",
        )
        return {
            "status": "completed",
            "evaluation_task_id": record.task_key,
            **summary,
        }


EvidenceCoverageUpdateService = Step6E4RefreshService


def refresh_step6e4_artifacts(task_id: str, *, store: ArtifactStore | None = None) -> dict[str, Any]:
    return Step6E4RefreshService(store=store).refresh(task_id)


def publish_step6e4_gap_tasks(task_id: str, *, store: ArtifactStore | None = None) -> dict[str, Any]:
    return AnalystGapPublisherService(store=store).publish(task_id)
