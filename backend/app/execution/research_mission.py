from __future__ import annotations

import hashlib
from typing import Any

from app.agents.web_evidence import normalize_dimension
from app.collection.service import competitor_context_matches
from app.harness.artifacts import ArtifactStore
from app.tools.router import research_intent_for_dimension
from app.schemas import (
    EvidenceCoverage,
    InformationNeed,
    OfficialConfidence,
    OfficialDomainContext,
    ResearchAgentBudget,
    ResearchAgentRun,
    ResearchMissionDecision,
    ResearchMission,
    ResearchMissionState,
    ResearchTask,
    ResearchWorkerResult,
    SourceDocument,
    SourceEvidence,
    SourceTaskAssociation,
    utc_now,
)


class ResearchMissionService:
    """Deterministic supervisor state around focused R1 Workers."""

    def __init__(self, *, store: ArtifactStore):
        self.store = store

    @staticmethod
    def _mission_id(task_id: str, competitor: str) -> str:
        identity = f"{task_id}|{competitor.casefold()}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"researchmission_{digest}"

    def ensure_missions(self, task_id: str) -> list[ResearchMission]:
        plans = self.store.load_many(task_id, "research_plans")
        decision_question = str(
            (plans[-1] if plans else {}).get("decision_question") or ""
        ).strip()
        needs = {
            item.id: item
            for item in (
                InformationNeed(**raw)
                for raw in self.store.load_many(
                    task_id, "research_information_needs"
                )
            )
        }
        tasks = [
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
        ]
        grouped: dict[str, list[ResearchTask]] = {}
        for task in tasks:
            if task.information_need_id not in needs:
                # Compatibility fixtures and the legacy path may predate real
                # InformationNeed ownership. They keep running outside Mission.
                continue
            grouped.setdefault(task.competitor.casefold(), []).append(task)

        existing = {
            item.id: item
            for item in (
                ResearchMission(**raw)
                for raw in self.store.load_many(task_id, "research_missions")
            )
        }
        missions: list[ResearchMission] = []
        states = {
            item.mission_id: item
            for item in (
                ResearchMissionState(**raw)
                for raw in self.store.load_many(
                    task_id, "research_mission_states"
                )
            )
        }
        known_run_ids = {
            str(raw.get("id") or "")
            for raw in self.store.load_many(task_id, "research_agent_runs")
        }
        for mission_id, state in list(states.items()):
            if any(
                run_id not in known_run_ids for run_id in state.worker_run_ids
            ):
                states[mission_id] = ResearchMissionState(
                    id=state.id,
                    task_id=state.task_id,
                    mission_id=state.mission_id,
                    competitor=state.competitor,
                    metadata={
                        **state.metadata,
                        "reset_reason": "worker_runs_no_longer_present",
                    },
                )
        for grouped_tasks in grouped.values():
            competitor = grouped_tasks[0].competitor
            mission_id = self._mission_id(task_id, competitor)
            prior = existing.get(mission_id)
            mission = ResearchMission(
                id=mission_id,
                task_id=task_id,
                competitor=competitor,
                goal=(
                    f"{decision_question}（研究对象：{competitor}）"
                    if decision_question
                    else "；".join(item.objective for item in grouped_tasks)
                ),
                information_need_ids=list(
                    dict.fromkeys(
                        item.information_need_id for item in grouped_tasks
                    )
                ),
                research_task_ids=[item.id for item in grouped_tasks],
                status=prior.status if prior else "active",
                created_at=prior.created_at if prior else utc_now(),
                updated_at=utc_now(),
                metadata={
                    **(prior.metadata if prior else {}),
                    "supervisor": "deterministic_r1_mission_v1",
                },
            )
            missions.append(mission)
            if mission_id not in states:
                states[mission_id] = ResearchMissionState(
                    id=f"missionstate_{mission_id.removeprefix('researchmission_')}",
                    task_id=task_id,
                    mission_id=mission_id,
                    competitor=competitor,
                )
        self.store.save_many(task_id, "research_missions", missions)
        self.store.save_many(
            task_id,
            "research_mission_states",
            [states[item.id] for item in missions],
        )
        return missions

    def mission_for_task(
        self, task_id: str, research_task_id: str
    ) -> ResearchMission | None:
        return next(
            (
                ResearchMission(**raw)
                for raw in self.store.load_many(task_id, "research_missions")
                if research_task_id in raw.get("research_task_ids", [])
            ),
            None,
        )

    def _state(
        self, task_id: str, mission_id: str
    ) -> ResearchMissionState:
        state = next(
            (
                ResearchMissionState(**raw)
                for raw in self.store.load_many(
                    task_id, "research_mission_states"
                )
                if raw.get("mission_id") == mission_id
            ),
            None,
        )
        if state is None:
            raise LookupError(f"ResearchMissionState 不存在：{mission_id}")
        return state

    def _save_state(self, state: ResearchMissionState) -> None:
        states = [
            ResearchMissionState(**raw)
            for raw in self.store.load_many(
                state.task_id, "research_mission_states"
            )
        ]
        self.store.save_many(
            state.task_id,
            "research_mission_states",
            [item for item in states if item.mission_id != state.mission_id]
            + [state],
        )

    def prepare_worker(
        self,
        task_id: str,
        research_task: ResearchTask,
        worker_budget: ResearchAgentBudget | None = None,
    ) -> dict[str, Any]:
        mission = self.mission_for_task(task_id, research_task.id)
        if mission is None:
            return {}
        state = self._state(task_id, mission.id)
        source_by_id = {
            item.id: item
            for item in (
                SourceDocument(**raw)
                for raw in self.store.load_many(task_id, "sources")
            )
            if item.id in state.source_ids
        }
        associations = [
            SourceTaskAssociation(**raw)
            for raw in self.store.load_many(
                task_id, "source_task_associations"
            )
        ]
        association_keys = {
            (item.research_task_id, item.source_id) for item in associations
        }
        for source in source_by_id.values():
            key = (research_task.id, source.id)
            if key in association_keys:
                continue
            identity = f"{task_id}|{research_task.id}|{source.id}"
            associations.append(
                SourceTaskAssociation(
                    id=(
                        "sourceassoc_"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
                    ),
                    task_id=task_id,
                    research_task_id=research_task.id,
                    source_id=source.id,
                    discovery_method="research_mission_shared_source",
                    requested_url=source.url,
                    metadata={"mission_id": mission.id},
                )
            )
        self.store.save_many(
            task_id, "source_task_associations", associations
        )
        from app.execution.research_mission_context import (
            ResearchMissionContextBuilder,
        )

        return ResearchMissionContextBuilder(store=self.store).build(
            task_id=task_id,
            mission=mission,
            state=state,
            research_task=research_task,
            worker_budget=worker_budget,
        ).model_dump(mode="json")

    def worker_dedup_state(
        self, task_id: str, research_task_id: str
    ) -> dict[str, list[str]]:
        mission = self.mission_for_task(task_id, research_task_id)
        if mission is None:
            return {"attempted_queries": [], "visited_urls": []}
        state = self._state(task_id, mission.id)
        return {
            "attempted_queries": list(state.attempted_queries),
            "visited_urls": list(state.visited_urls),
        }

    def merge_worker(
        self, task_id: str, research_task_id: str
    ) -> ResearchMissionState | None:
        mission = self.mission_for_task(task_id, research_task_id)
        if mission is None:
            return None
        task = next(
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
            if raw.get("id") == research_task_id
        )
        runs = [
            ResearchAgentRun(**raw)
            for raw in self.store.load_many(task_id, "research_agent_runs")
            if raw.get("research_task_id") == research_task_id
        ]
        if not runs:
            return self._state(task_id, mission.id)
        run = runs[-1]
        state = self._state(task_id, mission.id)
        expected_result_id = f"researchworkerresult_{run.id}"
        if (
            run.id in state.worker_run_ids
            and expected_result_id in state.worker_result_ids
        ):
            return state
        evidence_by_id = {
            item.id: item
            for item in (
                SourceEvidence(**raw)
                for raw in self.store.load_many(task_id, "evidence")
            )
        }
        source_ids = [
            evidence_by_id[evidence_id].source_id
            for evidence_id in run.verified_evidence_ids
            if evidence_id in evidence_by_id
        ]
        source_ids.extend(
            item.source_id
            for item in (
                SourceTaskAssociation(**raw)
                for raw in self.store.load_many(
                    task_id, "source_task_associations"
                )
            )
            if item.research_task_id == research_task_id
        )
        source_ids.extend(
            item.id
            for item in (
                SourceDocument(**raw)
                for raw in self.store.load_many(task_id, "sources")
            )
            if str(item.metadata.get("research_task_id") or "")
            == research_task_id
        )
        source_ids = list(dict.fromkeys(source_ids))
        confirmed_domains = [
            item.domain
            for item in (
                OfficialDomainContext(**raw)
                for raw in self.store.load_many(
                    task_id, "official_domain_contexts"
                )
            )
            if competitor_context_matches(item.competitor, mission.competitor)
            and item.confidence == OfficialConfidence.CONFIRMED.value
        ]
        outcome_by_need = dict(state.outcome_by_need)
        remaining_by_need = dict(state.remaining_need_by_id)
        outcome_by_need[task.information_need_id] = run.outcome
        if run.remaining_need:
            remaining_by_need[task.information_need_id] = run.remaining_need
        else:
            remaining_by_need.pop(task.information_need_id, None)
        worker_result = ResearchWorkerResult(
            id=expected_result_id,
            task_id=task_id,
            mission_id=mission.id,
            research_task_id=research_task_id,
            information_need_id=task.information_need_id,
            outcome=run.outcome,
            attempted_queries=run.attempted_queries,
            visited_urls=run.visited_urls,
            source_ids=source_ids,
            verified_evidence_ids=run.verified_evidence_ids,
            discovered_terms=[item.term for item in run.observed_terms],
            remaining_need=run.remaining_need,
            steps_used=run.step_count,
            searches_used=run.search_count,
            sources_used=run.source_count,
            completed_at=run.completed_at or utc_now(),
            metadata={"worker_run_id": run.id},
        )
        self._save_worker_result(worker_result)
        updated = state.model_copy(
            update={
                "attempted_queries": list(
                    dict.fromkeys(
                        [*state.attempted_queries, *run.attempted_queries]
                    )
                ),
                "visited_urls": list(
                    dict.fromkeys([*state.visited_urls, *run.visited_urls])
                ),
                "source_ids": list(
                    dict.fromkeys([*state.source_ids, *source_ids])
                ),
                "verified_evidence_ids": list(
                    dict.fromkeys(
                        [
                            *state.verified_evidence_ids,
                            *run.verified_evidence_ids,
                        ]
                    )
                ),
                "observed_terms": list(
                    dict.fromkeys(
                        [
                            *state.observed_terms,
                            *(item.term for item in run.observed_terms),
                        ]
                    )
                ),
                "confirmed_official_domains": list(
                    dict.fromkeys(
                        [
                            *state.confirmed_official_domains,
                            *confirmed_domains,
                        ]
                    )
                ),
                "worker_run_ids": list(
                    dict.fromkeys([*state.worker_run_ids, run.id])
                ),
                "worker_result_ids": list(
                    dict.fromkeys(
                        [*state.worker_result_ids, worker_result.id]
                    )
                ),
                "outcome_by_need": outcome_by_need,
                "remaining_need_by_id": remaining_by_need,
                "version": state.version + 1,
                "updated_at": utc_now(),
            }
        )
        self._save_state(updated)
        return updated

    def latest_worker_result(
        self, task_id: str, mission_id: str
    ) -> ResearchWorkerResult | None:
        state = self._state(task_id, mission_id)
        if not state.worker_result_ids:
            return None
        by_id = {
            item.id: item
            for item in (
                ResearchWorkerResult(**raw)
                for raw in self.store.load_many(
                    task_id, "research_worker_results"
                )
            )
        }
        return next(
            (
                by_id[result_id]
                for result_id in reversed(state.worker_result_ids)
                if result_id in by_id
            ),
            None,
        )

    def record_supervisor_decision(
        self, decision: ResearchMissionDecision
    ) -> ResearchMissionState:
        mission = next(
            (
                ResearchMission(**raw)
                for raw in self.store.load_many(
                    decision.task_id, "research_missions"
                )
                if raw.get("id") == decision.mission_id
            ),
            None,
        )
        if mission is None:
            raise LookupError(f"ResearchMission 不存在：{decision.mission_id}")
        if (
            decision.target_need
            and decision.target_need not in mission.information_need_ids
        ):
            raise ValueError("Supervisor target_need 不属于当前 Mission")
        decisions = [
            ResearchMissionDecision(**raw)
            for raw in self.store.load_many(
                decision.task_id, "research_mission_decisions"
            )
        ]
        self.store.save_many(
            decision.task_id,
            "research_mission_decisions",
            [item for item in decisions if item.id != decision.id]
            + [decision],
        )
        state = self._state(decision.task_id, decision.mission_id)
        updated = state.model_copy(
            update={
                "supervisor_decision_ids": list(
                    dict.fromkeys(
                        [*state.supervisor_decision_ids, decision.id]
                    )
                ),
                "version": state.version + 1,
                "updated_at": utc_now(),
            }
        )
        self._save_state(updated)
        return updated

    def materialize_research_unit(
        self,
        decision: ResearchMissionDecision,
        *,
        collection_round: int,
        max_collection_rounds: int,
    ) -> ResearchTask | None:
        if collection_round > max_collection_rounds:
            return None
        mission = next(
            (
                ResearchMission(**raw)
                for raw in self.store.load_many(
                    decision.task_id, "research_missions"
                )
                if raw.get("id") == decision.mission_id
            ),
            None,
        )
        if mission is None or decision.target_need not in mission.information_need_ids:
            raise ValueError("Supervisor 只能为 Mission 中真实 InformationNeed 创建 Research Unit")
        needs = {
            item.id: item
            for item in (
                InformationNeed(**raw)
                for raw in self.store.load_many(
                    decision.task_id, "research_information_needs"
                )
            )
        }
        need = needs.get(decision.target_need)
        if need is None:
            raise ValueError("Supervisor target_need 未引用真实 InformationNeed")
        tasks = [
            ResearchTask(**raw)
            for raw in self.store.load_many(decision.task_id, "research_tasks")
        ]
        existing = next(
            (
                item
                for item in tasks
                if item.information_need_id == need.id
                and item.competitor.casefold() == mission.competitor.casefold()
                and item.collection_round == collection_round
                and item.status == "waiting_for_collector"
            ),
            None,
        )
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "objective": decision.research_goal,
                    "stop_condition": (
                        existing.stop_condition
                        if existing.stop_condition
                        else decision.reason
                    ),
                    "metadata": {
                        **existing.metadata,
                        "mission_id": mission.id,
                        "supervisor_reason": decision.reason,
                        "supervisor_decision_ids": list(
                            dict.fromkeys(
                                [
                                    *existing.metadata.get(
                                        "supervisor_decision_ids", []
                                    ),
                                    decision.id,
                                ]
                            )
                        ),
                    },
                }
            )
            self.store.save_many(
                decision.task_id,
                "research_tasks",
                [updated if item.id == existing.id else item for item in tasks],
            )
            self.ensure_missions(decision.task_id)
            return updated
        parents = [
            item
            for item in tasks
            if item.information_need_id == need.id
            and item.competitor.casefold() == mission.competitor.casefold()
        ]
        parent = max(parents, key=lambda item: item.collection_round) if parents else None
        framework_provenance = (
            {
                "schema_version": parent.schema_version,
                "framework_id": parent.framework_id,
                "framework_version": parent.framework_version,
                "framework_dimension_id": parent.framework_dimension_id,
                "framework_content_hash": parent.framework_content_hash,
            }
            if parent and parent.framework_id
            else {}
        )
        identity = (
            f"{decision.task_id}|{mission.id}|{need.id}|{collection_round}"
        )
        research_unit = ResearchTask(
            **framework_provenance,
            id=(
                    "researchtask_mission_"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            ),
            task_id=decision.task_id,
            information_need_id=need.id,
            title=(
                f"Mission 第 {collection_round} 轮研究 "
                f"{mission.competitor} 的 {normalize_dimension(need.dimension)}"
            ),
            objective=decision.research_goal,
            competitor=mission.competitor,
            dimension=normalize_dimension(need.dimension),
            research_intent=(
                    need.research_intent
                    or research_intent_for_dimension(need.dimension)
            ),
            preferred_source_types=need.preferred_source_types,
            status="waiting_for_collector",
            stop_condition=(
                parent.stop_condition
                if parent and parent.stop_condition
                else decision.reason
            ),
            collection_round=collection_round,
            parent_research_task_id=parent.id if parent else "",
            assigned_role="researcher",
            metadata={
                "source": "r2_mission_supervisor",
                "mission_id": mission.id,
                "supervisor_decision_ids": [decision.id],
                "supervisor_reason": decision.reason,
            },
        )
        self.store.save_many(
            decision.task_id, "research_tasks", [*tasks, research_unit]
        )
        self.ensure_missions(decision.task_id)
        return research_unit

    def finish_mission(self, task_id: str, mission_id: str) -> None:
        missions = [
            ResearchMission(**raw)
            for raw in self.store.load_many(task_id, "research_missions")
        ]
        self.store.save_many(
            task_id,
            "research_missions",
            [
                item.model_copy(
                    update={"status": "finished", "updated_at": utc_now()}
                )
                if item.id == mission_id
                else item
                for item in missions
            ],
        )

    def _save_worker_result(self, result: ResearchWorkerResult) -> None:
        results = [
            ResearchWorkerResult(**raw)
            for raw in self.store.load_many(
                result.task_id, "research_worker_results"
            )
        ]
        self.store.save_many(
            result.task_id,
            "research_worker_results",
            [item for item in results if item.id != result.id] + [result],
        )

    def refresh_coverage(self, task_id: str) -> None:
        missions = self.ensure_missions(task_id)
        tasks = [
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
        ]
        coverage = [
            EvidenceCoverage(**raw)
            for raw in self.store.load_many(task_id, "evidence_coverage")
        ]
        for mission in missions:
            state = self._state(task_id, mission.id)
            by_need = dict(state.coverage_status_by_need)
            mission_tasks = [
                item for item in tasks if item.id in mission.research_task_ids
            ]
            for task in mission_tasks:
                match = next(
                    (
                        item
                        for item in coverage
                        if item.competitor.casefold()
                        == mission.competitor.casefold()
                        and normalize_dimension(item.dimension)
                        == normalize_dimension(task.dimension)
                    ),
                    None,
                )
                if match is not None:
                    by_need[task.information_need_id] = str(match.status)
            self._save_state(
                state.model_copy(
                    update={
                        "coverage_status_by_need": by_need,
                        "version": state.version + 1,
                        "updated_at": utc_now(),
                    }
                )
            )
