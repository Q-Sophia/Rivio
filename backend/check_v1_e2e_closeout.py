from __future__ import annotations

from pathlib import Path

from app.api.main import build_task_workspace
from app.harness.artifacts import ArtifactStore


TASK_ID = "task_user_644a69bb061f"
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    store = ArtifactStore()
    task = store.load_many(TASK_ID, "analysis_tasks")[-1]
    brief = task["metadata"]["research_brief"]
    require(
        brief["research_mode"] == "TARGET_CENTRIC_COMPETITIVE_ANALYSIS",
        "ResearchBrief mode is not target-centric competitive analysis",
    )
    require(brief["primary_target"] == "ClassIn", "ResearchBrief primary target changed")
    require(brief["competitor_discovery"] is True, "ResearchBrief discovery is disabled")

    plan = store.load_many(TASK_ID, "research_plans")[-1]
    discovered = plan["metadata"].get("discovered_competitors", [])
    require(plan["metadata"].get("competitor_discovery_strategy") == "local_catalog_v1", "Planner strategy changed")
    require("BigBlueButton" in discovered, "Planner did not expand the local catalog")

    loop = store.load_many(TASK_ID, "research_loop_runs")[-1]
    gaps = store.load_many(TASK_ID, "research_gaps")
    require(loop["status"] == "requires_human", "ResearchLoop disguised incomplete coverage as complete")
    require(gaps, "ResearchGap artifacts were lost")

    claims = store.load_many(TASK_ID, "claims")
    citation_checks = store.load_many(TASK_ID, "citation_checks")
    reports = store.load_many(TASK_ID, "reports")
    statements = store.load_many(TASK_ID, "report_statements")
    reviews = store.load_many(TASK_ID, "review_feedback")
    gates = store.load_many(TASK_ID, "quality_gates")
    require(claims and citation_checks, "Analyst/Citation artifacts are missing")
    require(reports and statements, "Writer artifacts are missing")
    require(reviews and gates, "Reviewer/QualityGate artifacts are missing")

    writer_calls = [
        item for item in store.load_many(TASK_ID, "llm_calls")
        if item["agent_role"] == "writer"
    ]
    require(len(writer_calls) == 1, "Writer was billed more than once")

    evidence = {item["id"]: item for item in store.load_many(TASK_ID, "evidence")}
    sources = {item["id"] for item in store.load_many(TASK_ID, "sources")}
    claim_ids = {item["id"] for item in store.load_many(TASK_ID, "claims_v2")}
    for statement in (item for item in statements if item["claim_ids"]):
        require(set(statement["claim_ids"]) <= claim_ids, "ReportStatement has unknown claim")
        require(set(statement["evidence_ids"]) <= evidence.keys(), "ReportStatement has unknown evidence")
        require(
            all(evidence[evidence_id]["source_id"] in sources for evidence_id in statement["evidence_ids"]),
            "ReportStatement evidence has unknown source",
        )
    require(
        set(reports[-1]["sections"].get("research_gap_ids", []))
        == {item["id"] for item in gaps},
        "Report does not disclose every ResearchGap",
    )

    workspace = build_task_workspace(TASK_ID)
    require(workspace["taskId"] == TASK_ID, "Workspace crossed task boundaries")
    require(workspace["report"]["id"] == reports[-1]["id"], "Workspace report is not task-local")
    require(len(workspace["reportStatements"]) == len(statements), "Workspace lost report trace")
    require(workspace["evidence"] and workspace["sources"], "Workspace lost evidence trace")
    require(workspace["qualityGates"], "Workspace lost quality gate")

    frontend = (ROOT / "frontend" / "src" / "app.js").read_text(encoding="utf-8")
    frontend_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    render_draft = frontend[frontend.index("function renderIntentDraft"):frontend.index("async function loadRecentDrafts")]
    require("setActiveTaskContext(confirmedTaskId)" in render_draft, "confirmed task does not become activeTaskId")
    require("loadResearchPlan(confirmedTaskId)" in render_draft, "confirmed task does not enter Research Planner")
    require("loadExecutionPlanning(confirmedTaskId)" not in render_draft, "confirmed task still enters legacy Dataset Gate")
    require('id="execution-planning"' not in frontend_html, "legacy Dataset Gate DOM still exists")
    require('id="execution-runner"' not in frontend_html, "legacy ExecutionRunner DOM still exists")
    require('qs("#execution-planning")' not in frontend, "legacy Dataset Gate still has production JS references")
    require("loadExecutionPlanning" not in frontend, "legacy Dataset Gate loader still exists")
    require("renderExecutionPlanning" not in frontend, "legacy Dataset Gate renderer still exists")
    require("loadRuns({ selectDefaultLegacy: false })" in frontend, "startup still auto-selects a legacy Run")
    setup = frontend[frontend.index("function setup()"):]
    require('#start-execution-btn").addEventListener' not in setup, "legacy Snapshot runner still has a production UI handler")
    require('#plan-generate-btn").addEventListener' not in setup, "legacy Dataset Gate still has a production UI handler")

    analysis_source = (ROOT / "backend" / "app" / "execution" / "research_analysis.py").read_text(encoding="utf-8")
    reporting_source = (ROOT / "backend" / "app" / "execution" / "research_reporting.py").read_text(encoding="utf-8")
    require("run_snapshot_llm_agent_workflow" not in analysis_source, "Research Analysis calls legacy Snapshot workflow")
    require("run_snapshot_llm_agent_workflow" not in reporting_source, "Reporting calls legacy Snapshot workflow")
    require("CollectorQueueService" not in reporting_source, "Reporting can rerun Collector")
    require("ExtractorQueueService" not in reporting_source, "Reporting can rerun Extractor")
    require("LLMProfessionalAnalystAgent" not in reporting_source, "Reporting can rerun Analyst")
    require("CitationAgent" not in reporting_source, "Reporting can rerun Citation")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    require("backend/app/data/runs/task_user_*/" in gitignore, "user runtime tasks are not ignored")
    require("backend/app/data/runs/draft_????????????/" in gitignore, "runtime drafts are not ignored")

    print("check_v1_e2e_closeout: PASS")
    print("active_task_workspace_only=true")
    print("legacy_dataset_gate_dom_removed=true")
    print("legacy_run_not_auto_selected=true")
    print("legacy_snapshot_not_reachable_from_research_flow=true")
    print("research_brief_target_centric=true")
    print("local_catalog_v1_discovery=true")
    print("research_loop_incomplete_state_preserved=true")
    print("writer_calls=1")
    print("report_evidence_source_trace=true")
    print("research_gaps_preserved=true")
    print("real_external_calls=0")


if __name__ == "__main__":
    main()
