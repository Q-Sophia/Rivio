from __future__ import annotations

import json
import re
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisTask,
    AuthorizeExecutionRequest,
    DatasetCompatibilityAssessment,
    DatasetCompatibilityStatus,
    ExecutionAuthorization,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionPlanStep,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from app.workflow.taskboard import TaskBoardStore


DATASET_ID = "online_education"
DATASET_LABEL = "在线教育实时互动与虚拟教室人工快照"
DATASET_DIR = Path(__file__).resolve().parents[1] / "data" / "snapshots" / DATASET_ID

COMPETITOR_ALIASES = {
    "ClassIn": {"classin"},
    "腾讯云实时互动 / TRTC 教育方案": {
        "腾讯云实时互动",
        "腾讯云trtc",
        "trtc",
        "trtc教育方案",
        "腾讯云实时互动trtc教育方案",
    },
    "BigBlueButton": {"bigbluebutton", "bbb"},
}

INDUSTRY_KEYWORDS = {
    "教育",
    "教学",
    "高校",
    "课堂",
    "网课",
    "在线教育",
    "onlineeducation",
    "virtualclassroom",
}

FOCUS_ALIASES = {
    "产品定位": {"定位", "产品定位", "positioning"},
    "产品能力": {"功能", "能力", "产品能力", "产品功能", "核心能力", "feature"},
    "定价与成本": {"价格", "定价", "成本", "pricing"},
    "生态与集成": {"生态", "集成", "生态集成", "api", "sdk", "ecosystem"},
    "目标客户": {"客户", "用户", "目标客户", "customer"},
    "风险与部署责任": {"风险", "部署", "部署责任", "部署运维", "运维", "合规", "risk"},
}


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())


def _load_snapshot_count(file_name: str) -> int:
    path = DATASET_DIR / file_name
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Snapshot file must be a list: {path}")
    return len(payload)


class ExecutionPlanningService:
    """Deterministic Step6D.3 dataset gate and execution-queue boundary."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()

    def get_task(self, task_id: str) -> AnalysisTask | None:
        path = self.store.root_dir / task_id / "analysis_tasks.json"
        if not path.is_file():
            return None
        items = self.store.load_many(task_id, "analysis_tasks")
        return AnalysisTask(**items[-1]) if items else None

    def get_latest_plan(self, task_id: str) -> ExecutionPlan | None:
        path = self.store.root_dir / task_id / "execution_plans.json"
        if not path.is_file():
            return None
        items = self.store.load_many(task_id, "execution_plans")
        return ExecutionPlan(**items[-1]) if items else None

    def get_latest_assessment(
        self,
        task_id: str,
    ) -> DatasetCompatibilityAssessment | None:
        path = self.store.root_dir / task_id / "dataset_compatibility_assessments.json"
        if not path.is_file():
            return None
        items = self.store.load_many(task_id, "dataset_compatibility_assessments")
        return DatasetCompatibilityAssessment(**items[-1]) if items else None

    def get_latest_authorization(self, task_id: str) -> ExecutionAuthorization | None:
        path = self.store.root_dir / task_id / "execution_authorizations.json"
        if not path.is_file():
            return None
        items = self.store.load_many(task_id, "execution_authorizations")
        return ExecutionAuthorization(**items[-1]) if items else None

    def build_plan(
        self,
        task_id: str,
    ) -> tuple[AnalysisTask, DatasetCompatibilityAssessment, ExecutionPlan]:
        task = self.get_task(task_id)
        if task is None:
            raise LookupError(f"未找到已确认的 AnalysisTask（分析任务）: {task_id}")
        if bool(task.metadata.get("execution_started")):
            raise ValueError("任务已经开始执行，不能重新生成启动前计划")
        if bool(task.metadata.get("execution_authorized")):
            raise ValueError("任务已经获得执行授权，不能重新生成计划")

        assessment = self._assess(task)
        compatible = assessment.status == DatasetCompatibilityStatus.COMPATIBLE.value
        plan = ExecutionPlan(
            task_id=task.id,
            compatibility_assessment_id=assessment.id,
            title=f"{task.report_subject or task.query}执行计划",
            dataset_id=DATASET_ID,
            steps=self._build_steps(compatible=compatible),
            estimated_real_llm_calls=2,
            estimated_mock_llm_calls=1,
            planned_model="deepseek-v4-flash",
            requires_explicit_authorization=True,
            authorization_available=compatible,
            status=(
                ExecutionPlanStatus.READY
                if compatible
                else ExecutionPlanStatus.BLOCKED
            ),
            metadata={
                "execution_boundary": "authorization_and_queue_only",
                "synchronous_workflow_execution": False,
                "crawler_enabled": False,
                "rag_enabled": False,
            },
        )
        updated_task = task.model_copy(
            update={
                "updated_at": utc_now(),
                "metadata": {
                    **task.metadata,
                    "dataset_compatibility": str(assessment.status),
                    "compatibility_assessment_id": assessment.id,
                    "execution_plan_id": plan.id,
                    "execution_status": str(plan.status),
                    "execution_started": False,
                },
            }
        )
        self.store.save_many(task.id, "analysis_tasks", [updated_task])
        self.store.save_many(task.id, "dataset_compatibility_assessments", [assessment])
        self.store.save_many(task.id, "execution_plans", [plan])
        return updated_task, assessment, plan

    def assess_task(self, task: AnalysisTask) -> DatasetCompatibilityAssessment:
        """Public read-only dataset assessment reused by research planning."""
        return self._assess(task)

    def authorize(
        self,
        task_id: str,
        request: AuthorizeExecutionRequest,
    ) -> tuple[AnalysisTask, ExecutionPlan, ExecutionAuthorization, dict]:
        if not request.acknowledge_dataset_scope:
            raise ValueError("必须明确确认当前资料范围后才能加入执行队列")
        task = self.get_task(task_id)
        plan = self.get_latest_plan(task_id)
        assessment = self.get_latest_assessment(task_id)
        if task is None or plan is None or assessment is None:
            raise LookupError("请先生成资料兼容评估与执行计划")
        if plan.id != request.plan_id:
            raise ValueError("执行计划已经变化，请刷新后重新确认")
        if plan.status == ExecutionPlanStatus.AUTHORIZED.value:
            existing = self.get_latest_authorization(task_id)
            if existing is None:
                raise ValueError("任务计划状态异常：缺少授权记录")
            board = TaskBoardStore(self.store).require_board(task_id)
            return task, plan, existing, board.model_dump(mode="json")
        if (
            assessment.status != DatasetCompatibilityStatus.COMPATIBLE.value
            or not plan.authorization_available
            or plan.status != ExecutionPlanStatus.READY.value
        ):
            raise ValueError("当前资料不完全兼容，不能授权执行")

        records = self._build_task_records(task_id, plan.id)
        board = TaskBoardStore(self.store).create_board(
            task_id=task_id,
            records=records,
            status=TaskStatus.READY,
            metadata={
                "source": "Step6D.3 execution authorization",
                "execution_plan_id": plan.id,
                "execution_started": False,
            },
        )
        authorization = ExecutionAuthorization(
            task_id=task_id,
            plan_id=plan.id,
            authorized=True,
            queue_status="queued",
            requested_by="user_ui",
            execution_started=False,
            metadata={"task_board_id": board.id},
        )
        authorized_plan = plan.model_copy(
            update={
                "status": ExecutionPlanStatus.AUTHORIZED,
                "updated_at": utc_now(),
                "metadata": {
                    **plan.metadata,
                    "authorization_id": authorization.id,
                    "task_board_id": board.id,
                    "execution_started": False,
                },
            }
        )
        updated_task = task.model_copy(
            update={
                "updated_at": utc_now(),
                "metadata": {
                    **task.metadata,
                    "execution_authorized": True,
                    "execution_status": "queued",
                    "execution_authorization_id": authorization.id,
                    "task_board_id": board.id,
                    "execution_started": False,
                },
            }
        )
        self.store.save_many(task_id, "analysis_tasks", [updated_task])
        self.store.save_many(task_id, "execution_plans", [authorized_plan])
        self.store.save_many(task_id, "execution_authorizations", [authorization])
        return (
            updated_task,
            authorized_plan,
            authorization,
            board.model_dump(mode="json"),
        )

    def _assess(self, task: AnalysisTask) -> DatasetCompatibilityAssessment:
        task_text = " ".join([task.industry, task.query, task.report_subject])
        normalized_task_text = _normalize(task_text)
        industry_match = any(
            _normalize(keyword) in normalized_task_text
            for keyword in INDUSTRY_KEYWORDS
        )

        alias_index: dict[str, str] = {}
        for canonical, aliases in COMPETITOR_ALIASES.items():
            alias_index[_normalize(canonical)] = canonical
            for alias in aliases:
                alias_index[_normalize(alias)] = canonical
        matched_map: dict[str, str] = {}
        missing_competitors: list[str] = []
        for requested in task.competitors:
            canonical = alias_index.get(_normalize(requested))
            if canonical:
                matched_map[requested] = canonical
            else:
                missing_competitors.append(requested)

        supported_focus: list[str] = []
        unsupported_focus: list[str] = []
        for requested in task.focus_areas:
            normalized = _normalize(requested)
            mapped = next(
                (
                    canonical
                    for canonical, aliases in FOCUS_ALIASES.items()
                    if normalized == _normalize(canonical)
                    or any(_normalize(alias) == normalized for alias in aliases)
                ),
                None,
            )
            if mapped:
                supported_focus.append(requested)
            else:
                unsupported_focus.append(requested)

        coverage = len(matched_map) / len(task.competitors) if task.competitors else 0.0
        blocking_reasons: list[str] = []
        warnings: list[str] = []
        if not industry_match:
            blocking_reasons.append("当前资料只覆盖在线教育实时互动与虚拟教室场景。")
        if missing_competitors:
            blocking_reasons.append(
                "当前资料缺少以下比较对象：" + "、".join(missing_competitors)
            )
        if unsupported_focus:
            blocking_reasons.append(
                "当前资料未建立以下分析维度：" + "、".join(unsupported_focus)
            )
        if industry_match and coverage == 1.0 and not unsupported_focus:
            status = DatasetCompatibilityStatus.COMPATIBLE
            recommended_action = "可以使用当前人工快照生成报告；仍需保留资料时效性提示。"
            warnings.append("当前资料是人工整理快照，不代表实时市场状态。")
        elif industry_match and coverage >= 0.5:
            status = DatasetCompatibilityStatus.PARTIAL
            recommended_action = "先补充缺失竞品或维度的来源证据，再重新评估。"
        else:
            status = DatasetCompatibilityStatus.INCOMPATIBLE
            recommended_action = "需要新建行业资料集或接入 WebCollector（网页采集器），不能复用当前快照。"

        return DatasetCompatibilityAssessment(
            task_id=task.id,
            dataset_id=DATASET_ID,
            dataset_label=DATASET_LABEL,
            status=status,
            industry_match=industry_match,
            requested_competitors=task.competitors,
            matched_competitors=list(matched_map),
            matched_competitor_map=matched_map,
            missing_competitors=missing_competitors,
            supported_focus_areas=supported_focus,
            unsupported_focus_areas=unsupported_focus,
            competitor_coverage_ratio=coverage,
            source_count=_load_snapshot_count("sources.json"),
            evidence_count=_load_snapshot_count("evidence.json"),
            blocking_reasons=blocking_reasons,
            warnings=warnings,
            recommended_action=recommended_action,
            metadata={
                "evaluation_method": "deterministic_dataset_profile_v1",
                "network_used": False,
                "llm_used": False,
            },
        )

    @staticmethod
    def _build_steps(*, compatible: bool) -> list[ExecutionPlanStep]:
        blocked_note = "资料闸门通过后执行" if compatible else "被资料兼容闸门阻止"
        return [
            ExecutionPlanStep(
                step_key="bind_snapshot",
                label="绑定并校验本地在线教育快照",
                agent_role=AgentRole.COLLECTOR,
                provider="local_snapshot",
                input_artifacts=["analysis_tasks"],
                output_artifacts=["sources", "evidence"],
                status="ready" if compatible else "blocked",
                note=blocked_note,
            ),
            ExecutionPlanStep(
                step_key="build_product_cards",
                label="抽取 ProductCard（产品卡片）",
                agent_role=AgentRole.EXTRACTOR,
                provider="mock-structured-v1",
                input_artifacts=["sources", "evidence"],
                output_artifacts=["product_cards"],
                status="planned" if compatible else "blocked",
                note="沿用经过回归验证的确定性抽取边界",
            ),
            ExecutionPlanStep(
                step_key="professional_analysis",
                label="生成专业分析结论与研究缺口",
                agent_role=AgentRole.ANALYST,
                provider="deepseek-v4-flash",
                input_artifacts=["analysis_tasks", "product_cards", "evidence"],
                output_artifacts=["claims_v2", "research_gaps"],
                status="planned" if compatible else "blocked",
            ),
            ExecutionPlanStep(
                step_key="citation_gate",
                label="执行 Citation Gate（引用闸门）",
                agent_role=AgentRole.CITATION,
                provider="deterministic",
                input_artifacts=["claims_v2", "evidence"],
                output_artifacts=["citation_checks", "claims"],
                status="planned" if compatible else "blocked",
            ),
            ExecutionPlanStep(
                step_key="write_report",
                label="撰写任务专属竞品分析报告",
                agent_role=AgentRole.WRITER,
                provider="deepseek-v4-flash",
                input_artifacts=["claims", "citation_checks", "research_gaps"],
                output_artifacts=["reports", "report_statements"],
                status="planned" if compatible else "blocked",
            ),
            ExecutionPlanStep(
                step_key="review_report",
                label="审查报告并执行 Quality Gate（质量闸门）",
                agent_role=AgentRole.REVIEWER,
                provider="deterministic",
                input_artifacts=["reports", "claims", "citation_checks"],
                output_artifacts=["review_feedback", "quality_gates"],
                status="planned" if compatible else "blocked",
            ),
        ]

    @staticmethod
    def _build_task_records(task_id: str, plan_id: str) -> list[TaskRecord]:
        specs = [
            ("collect_sources", TaskType.COLLECT_SOURCES, AgentRole.COLLECTOR, []),
            ("build_product_cards", TaskType.BUILD_PRODUCT_CARDS, AgentRole.EXTRACTOR, ["collect_sources"]),
            ("build_claims", TaskType.BUILD_CLAIMS, AgentRole.ANALYST, ["build_product_cards"]),
            ("check_citations", TaskType.CHECK_CITATIONS, AgentRole.CITATION, ["build_claims"]),
            ("build_report", TaskType.BUILD_REPORT, AgentRole.WRITER, ["check_citations"]),
            ("review_report", TaskType.REVIEW_REPORT, AgentRole.REVIEWER, ["build_report"]),
        ]
        return [
            TaskRecord(
                id=f"queue_{key}",
                task_id=task_id,
                task_key=key,
                task_type=task_type,
                target_agent_role=role,
                status=TaskStatus.READY if index == 0 else TaskStatus.PENDING,
                priority=TaskPriority.HIGH if index == 0 else TaskPriority.MEDIUM,
                depends_on=dependencies,
                input_refs=[f"execution_plan:{plan_id}"],
                reason="用户已授权 Step6D.3 执行计划；等待后台执行器领取。",
                max_attempts=2,
            )
            for index, (key, task_type, role, dependencies) in enumerate(specs)
        ]
