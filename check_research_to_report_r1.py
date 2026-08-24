from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from app.execution.research_reporting import ResearchReportingService
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig
from app.schemas import (
    AgentRole,
    AnalysisTask,
    LLMMode,
    LLMProvider,
    RunStatus,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.workflow.taskboard import TaskBoardStore


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "backend" / "app" / "data" / "runs" / "snapshot_step6c_professional_mock"
TASK_ID = "snapshot_step6c_professional_mock"
UPSTREAM_TYPES = [
    "sources",
    "evidence",
    "product_cards",
    "brief_assessments",
    "competitor_profiles",
    "evidence_coverage",
    "comparability_notes",
    "claims_v2",
    "research_gaps",
    "claims",
    "citation_checks",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(store: ArtifactStore, artifact_type: str) -> str:
    raw = json.dumps(
        store.load_many(TASK_ID, artifact_type),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prepare_store(root: Path) -> ArtifactStore:
    task_dir = root / TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    for artifact_type in UPSTREAM_TYPES:
        shutil.copy2(FIXTURE / f"{artifact_type}.json", task_dir / f"{artifact_type}.json")
    store = ArtifactStore(root_dir=root)
    brief = store.load_many(TASK_ID, "brief_assessments")[-1]
    store.save_many(
        TASK_ID,
        "analysis_tasks",
        [
            AnalysisTask(
                id=TASK_ID,
                task_id=TASK_ID,
                query=brief["decision_question"],
                industry=brief["industry"],
                competitors=["ClassIn", "腾讯云实时互动", "BigBlueButton"],
                focus_areas=list(brief["selected_dimensions"]),
                status=RunStatus.COMPLETED,
            )
        ],
    )
    citation_record = TaskRecord(
        id="queue_check_research_claim_citations",
        task_id=TASK_ID,
        task_key="check_research_claim_citations",
        task_type=TaskType.CHECK_CITATIONS,
        target_agent_role=AgentRole.CITATION,
        status=TaskStatus.COMPLETED,
        output_refs=["claims", "citation_checks"],
    )
    TaskBoardStore(store).create_board(
        task_id=TASK_ID,
        records=[citation_record],
        status=TaskStatus.COMPLETED,
    )
    for artifact_type in ("llm_calls", "llm_outputs", "dag_nodes", "agent_runs", "tool_calls"):
        store.save_many(TASK_ID, artifact_type, [])
    return store


def mock_client(store: ArtifactStore) -> LLMClient:
    return LLMClient(
        config=LLMConfig(
            provider=LLMProvider.MOCK,
            model="mock-structured-v1",
            mode=LLMMode.LLM,
            api_style="mock",
            max_retries=0,
        ),
        store=store,
    )


class ReviewerFailureService(ResearchReportingService):
    def _run_reviewer(self, task, recorder, tools) -> None:
        raise RuntimeError("offline reviewer failure fixture")


def copy_artifacts(source: ArtifactStore, target: ArtifactStore, artifact_types: list[str]) -> None:
    for artifact_type in artifact_types:
        source_path = source.root_dir / TASK_ID / f"{artifact_type}.json"
        target_path = target.root_dir / TASK_ID / f"{artifact_type}.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def run() -> dict:
    require(FIXTURE.exists(), "Step6C professional fixture is missing")
    temp_root = ROOT / "backend" / "app" / "data" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research_reporting_r1_", dir=temp_root) as temp:
        base = Path(temp)

        primary = prepare_store(base / "primary")
        before = {key: digest(primary, key) for key in UPSTREAM_TYPES}
        service = ResearchReportingService(store=primary, llm_client=mock_client(primary))
        initial = service.get_payload(TASK_ID)
        require(initial["stage"] == "awaiting_writer", "new flow must start at Writer")
        result = service.run(
            TASK_ID,
            mode="deepseek",
            acknowledge_real_llm_call=True,
        )
        require(result["completed"], "reporting flow did not complete")
        require(result["real_llm_calls_this_run"] == 1, "Writer must make exactly one call")
        for artifact_type in (
            "reports",
            "report_statements",
            "review_feedback",
            "quality_gates",
            "feedback_tasks",
        ):
            require((base / "primary" / TASK_ID / f"{artifact_type}.json").exists(), f"missing {artifact_type}.json")
        require(primary.load_many(TASK_ID, "reports"), "reports must be non-empty")
        require(primary.load_many(TASK_ID, "report_statements"), "report statements must be non-empty")
        require(primary.load_many(TASK_ID, "review_feedback"), "review must be non-empty")
        require(primary.load_many(TASK_ID, "quality_gates"), "quality gate must be non-empty")
        require(before == {key: digest(primary, key) for key in UPSTREAM_TYPES}, "upstream artifacts changed")

        claims = {item["id"]: item for item in primary.load_many(TASK_ID, "claims_v2")}
        evidence = {item["id"]: item for item in primary.load_many(TASK_ID, "evidence")}
        sources = {item["id"] for item in primary.load_many(TASK_ID, "sources")}
        claim_statements = [
            item for item in primary.load_many(TASK_ID, "report_statements") if item["claim_ids"]
        ]
        require(claim_statements, "no claim-backed report statements")
        for statement in claim_statements:
            require(set(statement["claim_ids"]) <= claims.keys(), "statement references unknown claim")
            require(set(statement["evidence_ids"]) <= evidence.keys(), "statement references unknown evidence")
            for evidence_id in statement["evidence_ids"]:
                require(evidence[evidence_id]["source_id"] in sources, "evidence references unknown source")
        report = primary.load_many(TASK_ID, "reports")[-1]
        require(
            set(report["sections"].get("research_gap_ids", []))
            == {item["id"] for item in primary.load_many(TASK_ID, "research_gaps")},
            "report must disclose all ResearchGap ids",
        )

        call_count = len(primary.load_many(TASK_ID, "llm_calls"))
        repeated = service.run(
            TASK_ID,
            mode="deepseek",
            acknowledge_real_llm_call=False,
        )
        require(repeated["real_llm_calls_this_run"] == 0, "completed retry billed Writer")
        require(len(primary.load_many(TASK_ID, "llm_calls")) == call_count, "completed retry wrote LLM call")

        report_only = prepare_store(base / "report_only")
        copy_artifacts(primary, report_only, ["reports", "report_statements"])
        report_only_result = ResearchReportingService(
            store=report_only,
            llm_client=mock_client(report_only),
        ).run(TASK_ID, mode="deepseek", acknowledge_real_llm_call=False)
        require(report_only_result["completed"], "report-only recovery failed")
        require(report_only_result["real_llm_calls_this_run"] == 0, "report-only recovery reran Writer")
        require(not report_only.load_many(TASK_ID, "llm_calls"), "report-only recovery created LLM call")

        report_without_statements = prepare_store(base / "report_without_statements")
        copy_artifacts(primary, report_without_statements, ["reports"])
        repaired = ResearchReportingService(
            store=report_without_statements,
            llm_client=mock_client(report_without_statements),
        ).run(TASK_ID, mode="deepseek", acknowledge_real_llm_call=False)
        require(repaired["completed"], "report statement recovery failed")
        require(report_without_statements.load_many(TASK_ID, "report_statements"), "statements were not repaired")
        require(not report_without_statements.load_many(TASK_ID, "llm_calls"), "statement repair reran Writer")

        review_only = prepare_store(base / "review_only")
        copy_artifacts(primary, review_only, ["reports", "report_statements", "review_feedback"])
        review_only_result = ResearchReportingService(
            store=review_only,
            llm_client=mock_client(review_only),
        ).run(TASK_ID, mode="deepseek", acknowledge_real_llm_call=False)
        require(review_only_result["completed"], "report+review recovery failed")
        require(review_only_result["real_llm_calls_this_run"] == 0, "gate recovery reran Writer")
        require(not review_only.load_many(TASK_ID, "llm_calls"), "gate recovery created LLM call")

        failed = prepare_store(base / "reviewer_failure")
        failing_service = ReviewerFailureService(store=failed, llm_client=mock_client(failed))
        try:
            failing_service.run(TASK_ID, mode="deepseek", acknowledge_real_llm_call=True)
        except RuntimeError as exc:
            require("reviewer failure" in str(exc), "unexpected reviewer failure")
        else:
            raise AssertionError("reviewer failure fixture did not fail")
        require(failed.load_many(TASK_ID, "reports"), "Writer report was lost after Reviewer failure")
        first_calls = len(failed.load_many(TASK_ID, "llm_calls"))
        recovered = ResearchReportingService(store=failed, llm_client=mock_client(failed)).run(
            TASK_ID,
            mode="deepseek",
            acknowledge_real_llm_call=False,
        )
        require(recovered["completed"], "Reviewer failure recovery failed")
        require(len(failed.load_many(TASK_ID, "llm_calls")) == first_calls, "recovery reran Writer")

        api_source = (ROOT / "backend" / "app" / "api" / "main.py").read_text(encoding="utf-8")
        require("mode=request.mode" in api_source, "POST does not wire request.mode")
        require(
            "acknowledge_real_llm_call=request.acknowledge_real_llm_call" in api_source,
            "POST does not wire acknowledgment",
        )
        require("run_research_reporting" in api_source and "..." not in api_source[api_source.index("def run_research_reporting"):api_source.index("def execution_status_payload")], "Reporting API wiring contains Ellipsis")

        frontend = (ROOT / "frontend" / "src" / "app.js").read_text(encoding="utf-8")
        require("runResearchReporting" in frontend, "frontend reporting action is missing")
        require(
            "acknowledge_real_llm_call: writerRequired" in frontend,
            "frontend does not acknowledge only the required Writer call",
        )

        return {
            "status": "PASS",
            "writer_calls": call_count,
            "report_statements": len(primary.load_many(TASK_ID, "report_statements")),
            "recovery_paths": 5,
            "external_calls": 0,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
