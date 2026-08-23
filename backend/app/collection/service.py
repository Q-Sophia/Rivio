from __future__ import annotations

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    CollectionAttempt,
    ResearchPlan,
    ResearchTask,
    SearchAttempt,
    SourceDocument,
    SourceType,
    TaskStatus,
    TaskRecord,
    TaskType,
    WebPageContent,
    WebSearchResult,
)
from app.tools.search_provider import SearchProvider, build_search_provider_from_env
from app.tools.web_collector import WebCollectorTool
from app.workflow.taskboard import TaskBoardStore, status_value


class CollectorQueueService:
    """Claim one bounded research task, discover URLs if needed, then collect pages."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        web_tool: WebCollectorTool | None = None,
        search_provider: SearchProvider | None = None,
        load_search_provider_from_env: bool = True,
    ):
        self.store = store or ArtifactStore()
        self.web_tool = web_tool or WebCollectorTool()
        self.search_provider = (
            search_provider
            if search_provider is not None
            else (
                build_search_provider_from_env()
                if load_search_provider_from_env
                else None
            )
        )
        self.board_store = TaskBoardStore(self.store)

    def close(self) -> None:
        self.web_tool.close()
        close = getattr(self.search_provider, "close", None)
        if callable(close):
            close()

    def run_once(self, task_id: str, *, max_new_sources: int | None = None) -> dict:
        research_tasks = [
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
        ]
        by_id = {item.id: item for item in research_tasks}
        ready = [
            record
            for record in self.board_store.ready_records(task_id)
            if status_value(record.target_agent_role) == AgentRole.COLLECTOR.value
            and record.task_key in by_id
            and by_id[record.task_key].status == "waiting_for_collector"
        ]
        if not ready:
            raise LookupError("没有可由 Collector Agent（采集智能体）领取的研究任务。")

        record = ready[0]
        research_task = by_id[record.task_key]
        plans = self.store.load_many(task_id, "research_plans")
        plan = ResearchPlan(**plans[-1]) if plans else None
        limit = plan.budget.max_sources_per_task if plan else 5
        if max_new_sources is not None:
            if max_new_sources < 1:
                raise ValueError("本轮没有剩余 SourceDocument（来源文档）预算。")
            limit = min(limit, max_new_sources)
        searched = False
        selected_search_result_ids: list[str] = []

        if not research_task.seed_urls:
            research_task, selected_search_result_ids = self._discover_seed_urls(
                task_id=task_id,
                research_task=research_task,
                limit=limit,
            )
            searched = True
            if research_task.seed_urls:
                research_tasks = [
                    research_task if item.id == research_task.id else item
                    for item in research_tasks
                ]
                self.store.save_many(task_id, "research_tasks", research_tasks)

        if not research_task.seed_urls:
            self.store.save_many(
                task_id,
                "research_tasks",
                [
                    item.model_copy(update={"status": "requires_human"})
                    if item.id == research_task.id
                    else item
                    for item in research_tasks
                ],
            )
            message = (
                "SearchProvider（搜索供应商）没有返回可安全采集的 URL。"
                if self.search_provider is not None
                else "缺少 Seed URL，且尚未配置可用的搜索供应商密钥。"
            )
            self.board_store.update_status(
                task_id,
                record.task_key,
                TaskStatus.REQUIRES_HUMAN,
                error=message,
                claimed_by_agent="web_collector_agent",
            )
            return {
                "research_task_id": research_task.id,
                "status": "requires_human",
                "collected_sources": 0,
                "search_used": searched,
                "message": message,
            }

        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.CLAIMED,
            claimed_by_agent="web_collector_agent",
        )
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.RUNNING,
            claimed_by_agent="web_collector_agent",
        )
        existing_sources = [
            SourceDocument(**item) for item in self.store.load_many(task_id, "sources")
        ]
        existing_urls = {item.url for item in existing_sources}
        web_pages = [
            WebPageContent(**item) for item in self.store.load_many(task_id, "web_pages")
        ]
        source_by_id = {item.id: item for item in existing_sources}
        existing_source_by_url = {item.url: item for item in existing_sources}
        for page in web_pages:
            source = source_by_id.get(page.source_id)
            if source is not None:
                existing_source_by_url.setdefault(page.requested_url, source)
                existing_source_by_url.setdefault(page.final_url, source)
        attempts = [
            CollectionAttempt(**item)
            for item in self.store.load_many(task_id, "collection_attempts")
        ]
        search_results = [
            WebSearchResult(**item)
            for item in self.store.load_many(task_id, "web_search_results")
        ]
        search_result_by_url = {
            item.url: item.id
            for item in search_results
            if item.id in selected_search_result_ids
        }
        new_sources: list[SourceDocument] = []
        reused_sources: list[SourceDocument] = []
        new_pages: list[WebPageContent] = []
        new_attempts: list[CollectionAttempt] = []

        for url in research_task.seed_urls[:limit]:
            if url in existing_source_by_url:
                reused = existing_source_by_url.get(url)
                if reused is not None and all(item.id != reused.id for item in reused_sources):
                    reused_sources.append(reused)
                continue
            try:
                fetched = self.web_tool.fetch(url)
                source_type = (
                    research_task.preferred_source_types[0]
                    if research_task.preferred_source_types
                    else SourceType.OTHER.value
                )
                source = SourceDocument(
                    task_id=task_id,
                    title=fetched.title or research_task.title,
                    url=fetched.final_url,
                    source_type=source_type,
                    competitor=research_task.competitor,
                    content_excerpt=fetched.text[:8000],
                    reliability_score=0.8 if not searched else 0.7,
                    metadata={
                        "collection_method": (
                            "search_then_web_collector_v1"
                            if searched
                            else "web_collector_seed_url_v1"
                        ),
                        "research_task_id": research_task.id,
                        "search_result_id": search_result_by_url.get(url, ""),
                        "content_hash": fetched.content_hash,
                        "render_mode": fetched.render_mode,
                        "browser_engine": fetched.browser_engine,
                    },
                )
                page = WebPageContent(
                    task_id=task_id,
                    source_id=source.id,
                    requested_url=url,
                    final_url=fetched.final_url,
                    title=fetched.title,
                    text=fetched.text,
                    content_type=fetched.content_type,
                    content_hash=fetched.content_hash,
                    render_mode=fetched.render_mode,
                    browser_engine=fetched.browser_engine,
                )
                attempt = CollectionAttempt(
                    task_id=task_id,
                    research_task_id=research_task.id,
                    requested_url=url,
                    final_url=fetched.final_url,
                    status="completed",
                    http_status=fetched.status_code,
                    source_document_id=source.id,
                    extracted_chars=len(fetched.text),
                    content_hash=fetched.content_hash,
                    render_mode=fetched.render_mode,
                    browser_engine=fetched.browser_engine,
                )
                new_sources.append(source)
                new_pages.append(page)
                new_attempts.append(attempt)
                existing_urls.add(fetched.final_url)
                existing_source_by_url[url] = source
                existing_source_by_url[fetched.final_url] = source
            except Exception as exc:
                new_attempts.append(
                    CollectionAttempt(
                        task_id=task_id,
                        research_task_id=research_task.id,
                        requested_url=url,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        self.store.save_many(task_id, "sources", existing_sources + new_sources)
        self.store.save_many(task_id, "web_pages", web_pages + new_pages)
        self.store.save_many(task_id, "collection_attempts", attempts + new_attempts)
        processed_sources = new_sources + reused_sources
        if processed_sources:
            self.store.save_many(
                task_id,
                "research_tasks",
                [
                    item.model_copy(update={"status": "collected"})
                    if item.id == research_task.id
                    else item
                    for item in research_tasks
                ],
            )
            output_refs = ["sources", "web_pages", "collection_attempts"]
            if searched:
                output_refs.extend(["search_attempts", "web_search_results"])
            self.board_store.update_status(
                task_id,
                record.task_key,
                TaskStatus.COMPLETED,
                output_refs=output_refs,
                claimed_by_agent="web_collector_agent",
            )
            extraction_task_key = f"extract_evidence_{research_task.id}"
            self.board_store.upsert_record(
                task_id,
                TaskRecord(
                    id=f"queue_{extraction_task_key}",
                    task_id=task_id,
                    task_key=extraction_task_key,
                    task_type=TaskType.EXTRACT_SOURCE_EVIDENCE,
                    target_agent_role=AgentRole.EXTRACTOR,
                    status=TaskStatus.PENDING,
                    priority=research_task.priority,
                    depends_on=[record.task_key],
                    input_refs=["sources", "web_pages", "research_tasks"],
                    reason=f"把 {research_task.competitor} 网页正文转换为可追溯证据",
                    metadata={
                        "research_task_id": research_task.id,
                        "source_ids": [item.id for item in processed_sources],
                        "max_evidence_per_page": 8,
                        "reused_source_count": len(reused_sources),
                    },
                ),
            )
            self.board_store.mark_ready_tasks(task_id)
            return {
                "research_task_id": research_task.id,
                "status": "completed",
                "collected_sources": len(new_sources),
                "reused_sources": len(reused_sources),
                "failed_urls": sum(item.status == "failed" for item in new_attempts),
                "browser_fallback_count": sum(
                    item.render_mode == "browser" for item in new_attempts
                ),
                "search_used": searched,
                "search_results_selected": len(selected_search_result_ids),
                "source_ids": [item.id for item in processed_sources],
                "extraction_task_id": extraction_task_key,
                "extraction_ready": True,
            }

        error = "全部候选 URL 采集失败；请查看 collection_attempts。"
        self.store.save_many(
            task_id,
            "research_tasks",
            [
                item.model_copy(update={"status": "collection_failed"})
                if item.id == research_task.id
                else item
                for item in research_tasks
            ],
        )
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.FAILED,
            error=error,
            claimed_by_agent="web_collector_agent",
        )
        return {
            "research_task_id": research_task.id,
            "status": "failed",
            "collected_sources": 0,
            "failed_urls": len(new_attempts),
            "search_used": searched,
            "message": error,
        }

    def _discover_seed_urls(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        limit: int,
    ) -> tuple[ResearchTask, list[str]]:
        if self.search_provider is None:
            return research_task, []
        attempts = [
            SearchAttempt(**item)
            for item in self.store.load_many(task_id, "search_attempts")
        ]
        results = [
            WebSearchResult(**item)
            for item in self.store.load_many(task_id, "web_search_results")
        ]
        selected_urls: list[str] = []
        selected_ids: list[str] = []
        queries = research_task.query_hints or [
            f"{research_task.competitor} {research_task.dimension} 官方资料"
        ]
        for query in queries[:3]:
            if len(selected_urls) >= limit:
                break
            attempt = SearchAttempt(
                task_id=task_id,
                research_task_id=research_task.id,
                provider=self.search_provider.name,
                query=query,
                status="running",
            )
            try:
                hits = self.search_provider.search(
                    query,
                    count=limit,
                    domain_filter=(research_task.preferred_domains[0] if research_task.preferred_domains else ""),
                )
                attempt = attempt.model_copy(
                    update={"status": "completed", "result_count": len(hits)}
                )
                for rank, hit in enumerate(hits, start=1):
                    selected = False
                    rejection = ""
                    try:
                        self.web_tool.url_policy.validate(hit.url)
                        if hit.url in selected_urls:
                            rejection = "duplicate_url"
                        elif len(selected_urls) >= limit:
                            rejection = "source_budget_reached"
                        else:
                            selected = True
                    except Exception as exc:
                        rejection = f"unsafe_url: {exc}"
                    result = WebSearchResult(
                        task_id=task_id,
                        research_task_id=research_task.id,
                        search_attempt_id=attempt.id,
                        provider=self.search_provider.name,
                        query=query,
                        rank=rank,
                        title=hit.title,
                        url=hit.url,
                        snippet=hit.snippet,
                        site_name=hit.site_name,
                        published_at=hit.published_at,
                        selected_for_collection=selected,
                        rejection_reason=rejection,
                    )
                    results.append(result)
                    if selected:
                        selected_urls.append(hit.url)
                        selected_ids.append(result.id)
            except Exception as exc:
                attempt = attempt.model_copy(
                    update={"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                )
            attempts.append(attempt)

        self.store.save_many(task_id, "search_attempts", attempts)
        self.store.save_many(task_id, "web_search_results", results)
        return research_task.model_copy(update={"seed_urls": selected_urls}), selected_ids
