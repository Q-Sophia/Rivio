from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.collection.source_quality import (
    MIN_COLLECTION_SCORE,
    SourceCandidateRanker,
    canonical_dimension,
    registrable_domain,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    CollectionAttempt,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    OfficialConfidence,
    OfficialDomainContext,
    ResearchPlan,
    ResearchTask,
    SearchAttempt,
    SourceDocument,
    SourceRole,
    SourceSelectionRun,
    SourceTaskAssociation,
    SourceType,
    RunStatus,
    TaskStatus,
    TaskRecord,
    TaskType,
    ToolCall,
    WebPageContent,
    WebSearchResult,
    utc_now,
)
from app.tools.search_provider import SearchProvider
from app.tools.search_transport import (
    NativeSearchToolTransport,
    SearchToolTransport,
    build_search_tool_transport_from_env,
)
from app.tools.web_collector import WebCollectorTool
from app.workflow.taskboard import TaskBoardStore, status_value
from app.workflow.trace import TraceRecorder


_FACTUAL_DIMENSIONS = {"positioning", "feature", "pricing", "ecosystem"}
_EXPERIENCE_OBJECTIVE_MARKERS = (
    "用户体验",
    "实际使用",
    "实测",
    "踩坑",
    "用户反馈",
    "社区反馈",
    "review",
    "experience",
)
_OFFICIAL_DISCOVERY_TERMS = {
    "positioning": "官网 about product 产品定位",
    "feature": "开发者文档 产品文档 API",
    "pricing": "官方定价 pricing billing",
    "ecosystem": "开放平台 integration marketplace API",
}
_FIRST_PARTY_TEXT_SIGNALS = (
    "官网",
    "官方",
    "开放平台",
    "开发者",
    "开发平台",
    "产品文档",
    "技术文档",
    "投资者关系",
    "official",
    "documentation",
    "developer",
    "developers",
    "open platform",
    "developer platform",
    "investor relations",
)
_NON_OFFICIAL_HOST_SUFFIXES = (
    "csdn.net",
    "zhihu.com",
    "medium.com",
    "juejin.cn",
    "jianshu.com",
    "cnblogs.com",
    "51cto.com",
    "segmentfault.com",
    "reddit.com",
    "wikipedia.org",
    "bilibili.com",
    "youtube.com",
    "36kr.com",
    "sohu.com",
    "163.com",
)

_OFFICIAL_DOMAIN_REGISTRY_TASK_ID = "official_domains"
_OFFICIAL_DOMAIN_CACHE_TTL_DAYS = 90
_OFFICIAL_SURFACE_LABELS = {
    "about",
    "api",
    "developer",
    "developers",
    "doc",
    "docs",
    "help",
    "investor",
    "investors",
    "ir",
    "open",
    "platform",
    "pricing",
    "product",
    "products",
    "support",
}
_EDITORIAL_PATH_LABELS = {
    "article",
    "articles",
    "blog",
    "blogs",
    "evaluating",
    "news",
    "post",
    "posts",
    "review",
    "reviews",
    "ucd",
}
_EDITORIAL_TITLE_MARKERS = (
    "一文",
    "产品分析",
    "体验报告",
    "对比评测",
    "深度剖析",
    "竞品分析",
    "评测",
    "测评",
    "盘点",
    "hands-on",
    "independent review",
)
_COMMUNITY_PROVIDER_NAMES = {"zhihu", "zhihu_search", "zhihu_mcp"}


def _is_official_first_task(research_task: ResearchTask) -> bool:
    dimension = canonical_dimension(research_task.dimension)
    objective = research_task.objective.casefold()
    return dimension in _FACTUAL_DIMENSIONS and not any(
        marker in objective for marker in _EXPERIENCE_OBJECTIVE_MARKERS
    )


def _official_discovery_queries(research_task: ResearchTask) -> list[str]:
    if not _is_official_first_task(research_task):
        return []
    dimension = canonical_dimension(research_task.dimension)
    targets = competitor_entities(research_task.competitor)
    values = [f"{target} 官网 官方文档" for target in targets]
    values.extend(
        f"{target} {_OFFICIAL_DISCOVERY_TERMS[dimension]}"
        for target in targets
    )
    return list(dict.fromkeys(" ".join(item.split()) for item in values))[:6]


def _official_targeted_query(research_task: ResearchTask, host: str) -> str:
    return " ".join(
        (
            f"site:{host}",
            research_task.competitor,
            research_task.dimension,
            research_task.objective,
        )
    )


def _contains_target_name(text: str, target: str) -> bool:
    normalized_text = " ".join(str(text or "").casefold().split())
    normalized_target = " ".join(str(target or "").casefold().split())
    if not normalized_target:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in normalized_target):
        return normalized_target in normalized_text
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_target)}(?![a-z0-9])",
            normalized_text,
        )
    )


def _normalized_official_host(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _url_is_within_domain(url: str, domain: str) -> bool:
    candidate_host = _normalized_official_host(url)
    normalized_domain = _normalized_official_host(f"https://{domain}")
    return bool(
        candidate_host
        and normalized_domain
        and (
            candidate_host == normalized_domain
            or candidate_host.endswith(f".{normalized_domain}")
        )
    )


def _is_known_non_official_host(host: str) -> bool:
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _NON_OFFICIAL_HOST_SUFFIXES
    )


def competitor_entities(value: str) -> list[str]:
    """Split comparison labels without coupling domain discovery to known brands."""

    normalized = " ".join(str(value or "").split())
    if not normalized:
        return []
    values = re.split(
        r"\s*(?:、|，|,|；|;|\||\bvs\.?\b|\bversus\b)\s*",
        normalized,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def competitor_context_matches(context_competitor: str, task_competitor: str) -> bool:
    context_values = {item.casefold() for item in competitor_entities(context_competitor)}
    task_values = {item.casefold() for item in competitor_entities(task_competitor)}
    return bool(context_values & task_values)


def _result_is_community_channel(candidate: WebSearchResult) -> bool:
    provider = str(candidate.provider or "").strip().casefold()
    source_tool = str(candidate.metadata.get("source_tool") or "").strip().casefold()
    channel = str(candidate.metadata.get("channel") or "").strip().casefold()
    return bool(
        provider in _COMMUNITY_PROVIDER_NAMES
        or source_tool in _COMMUNITY_PROVIDER_NAMES
        or channel == "zhihu"
    )


def _target_latin_tokens(target: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", str(target or "").casefold())
        if token not in {"and", "the", "with"}
    }


def _looks_editorial_candidate(candidate: WebSearchResult) -> bool:
    parsed = urlparse(candidate.url)
    path_labels = {
        item.casefold()
        for item in parsed.path.strip("/").split("/")
        if item
    }
    title = " ".join(str(candidate.title or "").casefold().split())
    return bool(
        path_labels & _EDITORIAL_PATH_LABELS
        or any(marker in title for marker in _EDITORIAL_TITLE_MARKERS)
    )


def _official_identity_signals(
    candidate: WebSearchResult,
    *,
    target: str,
) -> dict[str, bool]:
    host = _normalized_official_host(candidate.url)
    registered = registrable_domain(host)
    parsed = urlparse(candidate.url)
    path_labels = {
        item.casefold()
        for item in parsed.path.strip("/").split("/")
        if item
    }
    host_labels = set(host.split("."))
    main_label = registered.split(".")[0] if registered else ""
    latin_tokens = _target_latin_tokens(target)
    site_name = " ".join(str(candidate.site_name or "").casefold().split())
    normalized_host = host.casefold().removeprefix("www.")
    site_identity = bool(
        site_name
        and site_name not in {host.casefold(), normalized_host, registered.casefold()}
        and _contains_target_name(site_name, target)
    )
    title_identity = _contains_target_name(candidate.title, target)
    title_starts_target = str(candidate.title or "").strip().casefold().startswith(
        str(target or "").strip().casefold()
    )
    host_identity = bool(
        latin_tokens
        and (
            main_label in latin_tokens
            or any(token in host_labels for token in latin_tokens)
            or any(token in main_label for token in latin_tokens)
        )
    )
    platform_surface = bool(
        not parsed.path.strip("/")
        or path_labels & _OFFICIAL_SURFACE_LABELS
        or host_labels & _OFFICIAL_SURFACE_LABELS
    )
    return {
        "host_identity": host_identity,
        "site_identity": site_identity,
        "title_identity": title_identity,
        "title_starts_target": title_starts_target,
        "platform_surface": platform_surface,
        "editorial": _looks_editorial_candidate(candidate),
    }


def _probable_official_host(
    candidate: WebSearchResult,
    *,
    target: str,
) -> str:
    """Infer only a probable host from bounded local SearchResult signals."""

    if candidate.rank < 1 or candidate.rank > 10:
        return ""
    if _result_is_community_channel(candidate):
        return ""
    searchable_text = f"{candidate.title}\n{candidate.snippet}"
    if not _contains_target_name(searchable_text, target):
        return ""
    normalized_text = searchable_text.casefold()
    if not any(signal in normalized_text for signal in _FIRST_PARTY_TEXT_SIGNALS):
        return ""
    host = _normalized_official_host(candidate.url)
    if not host or _is_known_non_official_host(host):
        return ""
    signals = _official_identity_signals(candidate, target=target)
    if signals["editorial"]:
        return ""
    if not signals["platform_surface"]:
        return ""
    if not (
        signals["host_identity"]
        or signals["site_identity"]
        or signals["title_starts_target"]
    ):
        return ""
    return host


def _confirmed_official_host(
    candidate: WebSearchResult,
    *,
    target: str,
) -> str:
    """Confirm only direct domain/site identity; title text alone is insufficient."""

    host = _probable_official_host(candidate, target=target)
    if not host:
        return ""
    signals = _official_identity_signals(candidate, target=target)
    if signals["host_identity"] or signals["site_identity"]:
        return host
    return ""


def _candidate_official_host(
    candidate: WebSearchResult,
    *,
    target: str,
) -> str:
    """Use text only to discover a candidate; never to grant official authority."""

    if candidate.rank < 1 or candidate.rank > 10:
        return ""
    if _result_is_community_channel(candidate):
        return ""
    searchable_text = f"{candidate.title}\n{candidate.snippet}"
    if not _contains_target_name(searchable_text, target):
        return ""
    signals = _official_identity_signals(candidate, target=target)
    has_official_text = any(
        signal in searchable_text.casefold()
        for signal in _FIRST_PARTY_TEXT_SIGNALS
    )
    # A brand-titled home/product surface may enter verification without the
    # word "official".  This only discovers a probable candidate; ownership is
    # still decided later from direct identity or multiple structural signals.
    if not has_official_text and not (
        signals["title_starts_target"]
        and signals["platform_surface"]
        and not signals["editorial"]
    ):
        return ""
    return _normalized_official_host(candidate.url)


def _domain_resolution_confidence(
    *,
    domain: str,
    target: str,
    candidates: list[WebSearchResult],
) -> OfficialConfidence:
    """Resolve one Web candidate domain from independent structural signals."""

    matching = [
        item
        for item in candidates
        if _url_is_within_domain(item.url, domain)
        and not _result_is_community_channel(item)
    ]
    if not matching or _is_known_non_official_host(domain):
        return OfficialConfidence.REJECTED

    non_editorial = [item for item in matching if not _looks_editorial_candidate(item)]
    if not non_editorial:
        return OfficialConfidence.REJECTED

    signals = [
        _official_identity_signals(item, target=target)
        for item in non_editorial
    ]
    direct_identity = any(
        item["host_identity"] or item["site_identity"]
        for item in signals
    )
    official_surfaces = [
        item
        for item, item_signals in zip(non_editorial, signals)
        if item_signals["platform_surface"]
        and item_signals["title_identity"]
    ]
    surface_kinds: set[str] = set()
    ownership_surface = False
    for item in official_surfaces:
        parsed = urlparse(item.url)
        labels = {
            value.casefold()
            for value in (
                [*parsed.hostname.split(".")] if parsed.hostname else []
            )
        }
        labels.update(
            value.casefold()
            for value in parsed.path.strip("/").split("/")
            if value
        )
        if labels & {"about", "company", "legal", "privacy", "terms"}:
            surface_kinds.add("ownership")
            ownership_surface = True
        if labels & {"api", "developer", "developers", "doc", "docs"}:
            surface_kinds.add("documentation")
        if labels & {"open", "platform", "product", "products", "pricing"}:
            surface_kinds.add("product")
        if not parsed.path.strip("/"):
            surface_kinds.add("home")

    distinct_urls = {item.url for item in official_surfaces}
    structural_consensus = bool(
        len(distinct_urls) >= 2
        and len(surface_kinds) >= 2
        and (ownership_surface or "documentation" in surface_kinds)
    )
    if direct_identity and official_surfaces:
        return OfficialConfidence.CONFIRMED
    if structural_consensus:
        return OfficialConfidence.CONFIRMED
    return OfficialConfidence.PROBABLE


class CollectorQueueService:
    """Claim one bounded research task, discover URLs if needed, then collect pages."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        web_tool: WebCollectorTool | None = None,
        search_provider: SearchProvider | None = None,
        search_transport: SearchToolTransport | None = None,
        source_ranker: SourceCandidateRanker | None = None,
        load_search_provider_from_env: bool = True,
    ):
        self.store = store or ArtifactStore()
        self.web_tool = web_tool or WebCollectorTool()
        if search_transport is not None:
            self.search_transport = search_transport
        elif search_provider is not None:
            self.search_transport = NativeSearchToolTransport(search_provider)
        elif load_search_provider_from_env:
            self.search_transport = build_search_tool_transport_from_env()
        else:
            self.search_transport = None
        self.source_ranker = source_ranker or SourceCandidateRanker()
        self.board_store = TaskBoardStore(self.store)
        self.official_domain_registry = ArtifactStore(
            self.store.root_dir.parent / "cache"
        )

    def close(self) -> None:
        self.web_tool.close()
        close = getattr(self.search_transport, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _official_context_domain(value: str) -> str:
        host = _normalized_official_host(value)
        if not host and "://" not in value:
            host = _normalized_official_host(f"https://{value}")
        domain = registrable_domain(host)
        if not domain or _is_known_non_official_host(domain):
            return ""
        return domain

    def _confirmed_official_domains(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
    ) -> list[str]:
        raw_domains: list[str] = []
        for key in ("official_domains", "confirmed_official_domains"):
            values = research_task.metadata.get(key, [])
            if isinstance(values, str):
                values = [values]
            raw_domains.extend(str(item) for item in values)
        raw_domains.extend(
            item.domain
            for item in (
                OfficialDomainContext(**value)
                for value in self.store.load_many(
                    task_id,
                    "official_domain_contexts",
                )
            )
            if competitor_context_matches(
                item.competitor,
                research_task.competitor,
            )
            and item.confidence == OfficialConfidence.CONFIRMED.value
        )
        raw_domains.extend(
            item.domain
            for item in self._cached_official_domain_contexts(research_task)
            if item.confidence == OfficialConfidence.CONFIRMED.value
        )
        domains: list[str] = []
        for value in raw_domains:
            domain = self._official_context_domain(value)
            if domain and domain not in domains:
                domains.append(domain)
        return domains[:3]

    def _confirmed_official_entities(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
    ) -> set[str]:
        entities = competitor_entities(research_task.competitor)
        contexts = [
            OfficialDomainContext(**value)
            for value in self.store.load_many(task_id, "official_domain_contexts")
        ]
        contexts.extend(self._cached_official_domain_contexts(research_task))
        confirmed = {
            entity.casefold()
            for entity in entities
            if any(
                item.confidence == OfficialConfidence.CONFIRMED.value
                and competitor_context_matches(item.competitor, entity)
                for item in contexts
            )
        }
        if len(entities) == 1 and self._confirmed_official_domains(
            task_id=task_id,
            research_task=research_task,
        ):
            confirmed.add(entities[0].casefold())
        return confirmed

    def _probable_official_domains(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
    ) -> list[str]:
        raw_domains: list[str] = list(research_task.preferred_domains)
        values = research_task.metadata.get("probable_official_domains", [])
        if isinstance(values, str):
            values = [values]
        raw_domains.extend(str(item) for item in values)
        raw_domains.extend(
            item.domain
            for item in (
                OfficialDomainContext(**value)
                for value in self.store.load_many(
                    task_id,
                    "official_domain_contexts",
                )
            )
            if competitor_context_matches(
                item.competitor,
                research_task.competitor,
            )
            and item.confidence == OfficialConfidence.PROBABLE.value
        )
        raw_domains.extend(
            item.domain
            for item in self._cached_official_domain_contexts(research_task)
            if item.confidence == OfficialConfidence.PROBABLE.value
        )
        domains: list[str] = []
        for value in raw_domains:
            domain = self._official_context_domain(value)
            if domain and domain not in domains:
                domains.append(domain)
        return domains[:3]

    def _rejected_official_domains(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
    ) -> list[str]:
        contexts = [
            OfficialDomainContext(**value)
            for value in self.store.load_many(task_id, "official_domain_contexts")
        ]
        contexts.extend(self._cached_official_domain_contexts(research_task))
        domains: list[str] = []
        for item in contexts:
            if not competitor_context_matches(
                item.competitor,
                research_task.competitor,
            ):
                continue
            if item.confidence != OfficialConfidence.REJECTED.value:
                continue
            domain = registrable_domain(item.domain)
            if domain and domain not in domains:
                domains.append(domain)
        return domains

    def _resolve_and_record_official_domains(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        candidates: list[WebSearchResult],
        verification_method: str,
    ) -> dict[str, list[str]]:
        resolved: dict[str, list[str]] = {
            OfficialConfidence.CONFIRMED.value: [],
            OfficialConfidence.PROBABLE.value: [],
            OfficialConfidence.REJECTED.value: [],
        }
        for competitor in competitor_entities(research_task.competitor):
            candidate_domains: list[str] = []
            for item in sorted(candidates, key=lambda value: (value.rank, value.id)):
                host = _candidate_official_host(item, target=competitor)
                domain = registrable_domain(host)
                if domain and domain not in candidate_domains:
                    candidate_domains.append(domain)
                if len(candidate_domains) >= 3:
                    break
            for domain in candidate_domains:
                related = [
                    item
                    for item in candidates
                    if _url_is_within_domain(item.url, domain)
                ]
                confidence = _domain_resolution_confidence(
                    domain=domain,
                    target=competitor,
                    candidates=related,
                )
                self._record_official_domains(
                    task_id=task_id,
                    research_task=research_task,
                    domain_results={
                        domain: [item.id for item in related]
                    },
                    confidence=confidence,
                    competitor=competitor,
                    verification_method=verification_method,
                )
                value = confidence.value
                if domain not in resolved[value]:
                    resolved[value].append(domain)
        return resolved

    def _cached_official_domain_contexts(
        self,
        research_task: ResearchTask,
    ) -> list[OfficialDomainContext]:
        now = utc_now()
        contexts: list[OfficialDomainContext] = []
        for raw in self.official_domain_registry.load_many(
            _OFFICIAL_DOMAIN_REGISTRY_TASK_ID,
            "official_domain_contexts",
        ):
            item = OfficialDomainContext(**raw)
            if not competitor_context_matches(
                item.competitor,
                research_task.competitor,
            ):
                continue
            expires_at = str(item.metadata.get("expires_at") or "").strip()
            if expires_at:
                try:
                    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed <= now:
                    continue
            contexts.append(item)
        return contexts

    @staticmethod
    def _stronger_official_confidence(
        current: OfficialConfidence | str,
        candidate: OfficialConfidence | str,
    ) -> OfficialConfidence:
        priority = {
            OfficialConfidence.UNKNOWN.value: 0,
            OfficialConfidence.PROBABLE.value: 1,
            OfficialConfidence.REJECTED.value: 2,
            OfficialConfidence.CONFIRMED.value: 3,
        }
        current_value = str(getattr(current, "value", current))
        candidate_value = str(getattr(candidate, "value", candidate))
        selected = (
            candidate_value
            if priority.get(candidate_value, 0) > priority.get(current_value, 0)
            else current_value
        )
        return OfficialConfidence(selected)

    def _save_official_domain_registry(
        self,
        context: OfficialDomainContext,
    ) -> None:
        contexts = [
            OfficialDomainContext(**item)
            for item in self.official_domain_registry.load_many(
                _OFFICIAL_DOMAIN_REGISTRY_TASK_ID,
                "official_domain_contexts",
            )
        ]
        key = (context.competitor.casefold(), context.domain)
        existing = next(
            (
                item
                for item in contexts
                if (item.competitor.casefold(), item.domain) == key
            ),
            None,
        )
        registry_id = (
            "officialdomain_"
            + hashlib.sha256(
                f"registry|{context.competitor.casefold()}|{context.domain}".encode(
                    "utf-8"
                )
            ).hexdigest()[:12]
        )
        if existing is None:
            contexts.append(
                context.model_copy(
                    update={
                        "id": registry_id,
                        "task_id": _OFFICIAL_DOMAIN_REGISTRY_TASK_ID,
                    }
                )
            )
        else:
            confidence = self._stronger_official_confidence(
                existing.confidence,
                context.confidence,
            )
            contexts[contexts.index(existing)] = existing.model_copy(
                update={
                    "confidence": confidence,
                    "research_task_ids": list(
                        dict.fromkeys(
                            [
                                *existing.research_task_ids,
                                *context.research_task_ids,
                            ]
                        )
                    ),
                    "search_result_ids": list(
                        dict.fromkeys(
                            [
                                *existing.search_result_ids,
                                *context.search_result_ids,
                            ]
                        )
                    ),
                    "metadata": {**existing.metadata, **context.metadata},
                }
            )
        self.official_domain_registry.save_many(
            _OFFICIAL_DOMAIN_REGISTRY_TASK_ID,
            "official_domain_contexts",
            contexts,
        )

    def _record_official_domains(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        domain_results: dict[str, list[str]],
        confidence: OfficialConfidence = OfficialConfidence.PROBABLE,
        competitor: str | None = None,
        verification_method: str = "web_search_candidate",
    ) -> None:
        if not domain_results:
            return
        contexts = [
            OfficialDomainContext(**item)
            for item in self.store.load_many(task_id, "official_domain_contexts")
        ]
        by_key = {
            (item.competitor.casefold(), item.domain): item
            for item in contexts
        }
        resolved_competitor = competitor or research_task.competitor
        verified_at = utc_now()
        expires_at = verified_at + timedelta(days=_OFFICIAL_DOMAIN_CACHE_TTL_DAYS)
        for raw_domain, result_ids in domain_results.items():
            if confidence == OfficialConfidence.REJECTED:
                rejected_host = _normalized_official_host(raw_domain)
                if not rejected_host and "://" not in raw_domain:
                    rejected_host = _normalized_official_host(
                        f"https://{raw_domain}"
                    )
                domain = registrable_domain(rejected_host)
            else:
                domain = self._official_context_domain(raw_domain)
            if not domain:
                continue
            key = (resolved_competitor.casefold(), domain)
            existing = by_key.get(key)
            if existing is None:
                identity = f"{task_id}|{resolved_competitor.casefold()}|{domain}"
                existing = OfficialDomainContext(
                    id=(
                        "officialdomain_"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
                    ),
                    task_id=task_id,
                    competitor=resolved_competitor,
                    domain=domain,
                    confidence=confidence,
                )
                contexts.append(existing)
            resolved_confidence = self._stronger_official_confidence(
                existing.confidence,
                confidence,
            )
            updated = existing.model_copy(
                update={
                    "confidence": resolved_confidence,
                    "research_task_ids": list(
                        dict.fromkeys(
                            [*existing.research_task_ids, research_task.id]
                        )
                    ),
                    "search_result_ids": list(
                        dict.fromkeys(
                            [*existing.search_result_ids, *result_ids]
                        )
                    ),
                    "metadata": {
                        **existing.metadata,
                        "provider": (
                            self.search_transport.provider_name
                            if self.search_transport is not None
                            else ""
                        ),
                        "verification_method": verification_method,
                        "verified_at": verified_at.isoformat(),
                        "expires_at": expires_at.isoformat(),
                    },
                }
            )
            contexts[contexts.index(existing)] = updated
            by_key[key] = updated
            self._save_official_domain_registry(updated)
        self.store.save_many(task_id, "official_domain_contexts", contexts)

    def _associate_reused_source(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        source: SourceDocument,
        requested_url: str,
        search_result: WebSearchResult | None,
        discovery_method: str,
    ) -> str:
        discovered_by_current_task = bool(
            search_result is not None
            and search_result.research_task_id == research_task.id
            and search_result.url == requested_url
        ) or requested_url in research_task.seed_urls
        if not discovered_by_current_task:
            return ""
        identity = f"{task_id}|{research_task.id}|{source.id}"
        association_id = (
            "sourceassoc_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        )
        associations = [
            SourceTaskAssociation(**item)
            for item in self.store.load_many(task_id, "source_task_associations")
        ]
        if all(item.id != association_id for item in associations):
            associations.append(
                SourceTaskAssociation(
                    id=association_id,
                    task_id=task_id,
                    research_task_id=research_task.id,
                    source_id=source.id,
                    discovery_method=discovery_method,
                    search_result_id=search_result.id if search_result else "",
                    requested_url=requested_url,
                )
            )
            self.store.save_many(
                task_id,
                "source_task_associations",
                associations,
            )
        return association_id

    def fetch_and_persist_url(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        url: str,
        search_result_id: str = "",
        collection_method: str = "web_collector_seed_url_v1",
        reliability_score: float = 0.7,
        source_type_hint: str = "",
    ) -> dict:
        """Deterministically fetch one Agent-selected URL and persist V1 artifacts."""
        existing_sources = [
            SourceDocument(**item)
            for item in self.store.load_many(task_id, "sources")
        ]
        pages = [
            WebPageContent(**item)
            for item in self.store.load_many(task_id, "web_pages")
        ]
        attempts = [
            CollectionAttempt(**item)
            for item in self.store.load_many(task_id, "collection_attempts")
        ]
        search_result = next(
            (
                WebSearchResult(**item)
                for item in self.store.load_many(task_id, "web_search_results")
                if item.get("id") == search_result_id
            ),
            None,
        )
        source_by_id = {item.id: item for item in existing_sources}
        existing_source_by_url = {item.url: item for item in existing_sources}
        for page in pages:
            source = source_by_id.get(page.source_id)
            if source is not None:
                existing_source_by_url.setdefault(page.requested_url, source)
                existing_source_by_url.setdefault(page.final_url, source)

        existing = existing_source_by_url.get(url)
        if existing is not None:
            association_id = self._associate_reused_source(
                task_id=task_id,
                research_task=research_task,
                source=existing,
                requested_url=url,
                search_result=search_result,
                discovery_method="search_or_seed_url_reuse",
            )
            return {
                "status": "completed",
                "source_id": existing.id,
                "url": existing.url,
                "final_url": existing.url,
                "reused": True,
                "collection_attempt_id": "",
                "source_task_association_id": association_id,
            }

        try:
            fetched = self.web_tool.fetch(url)
        except Exception as exc:
            attempt = CollectionAttempt(
                task_id=task_id,
                research_task_id=research_task.id,
                requested_url=url,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            self.store.save_many(
                task_id,
                "collection_attempts",
                [*attempts, attempt],
            )
            return {
                "status": "failed",
                "requested_url": url,
                "error": attempt.error,
                "collection_attempt_id": attempt.id,
                "reused": False,
            }

        redirected_existing = existing_source_by_url.get(fetched.final_url)
        if redirected_existing is not None:
            attempt = CollectionAttempt(
                task_id=task_id,
                research_task_id=research_task.id,
                requested_url=url,
                final_url=fetched.final_url,
                status="completed",
                http_status=fetched.status_code,
                source_document_id=redirected_existing.id,
                extracted_chars=len(fetched.text),
                content_hash=fetched.content_hash,
                render_mode=fetched.render_mode,
                browser_engine=fetched.browser_engine,
                metadata={"reused_after_redirect": True},
            )
            self.store.save_many(
                task_id,
                "collection_attempts",
                [*attempts, attempt],
            )
            association_id = self._associate_reused_source(
                task_id=task_id,
                research_task=research_task,
                source=redirected_existing,
                requested_url=url,
                search_result=search_result,
                discovery_method="search_or_seed_url_redirect_reuse",
            )
            return {
                "status": "completed",
                "source_id": redirected_existing.id,
                "url": redirected_existing.url,
                "final_url": redirected_existing.url,
                "reused": True,
                "collection_attempt_id": attempt.id,
                "render_mode": fetched.render_mode,
                "browser_engine": fetched.browser_engine,
                "source_task_association_id": association_id,
            }

        confirmed_first_party = bool(
            search_result and search_result.metadata.get("first_party")
        ) or any(
            _url_is_within_domain(fetched.final_url, domain)
            for domain in self._confirmed_official_domains(
                task_id=task_id,
                research_task=research_task,
            )
        )
        preferred_source_types = list(research_task.preferred_source_types)
        source_type = (
            preferred_source_types[0]
            if preferred_source_types
            else SourceType.OTHER.value
        )
        tool_source_type = (
            str(search_result.metadata.get("source_type") or "")
            if search_result
            else str(source_type_hint or "")
        )
        if tool_source_type == "community" and not confirmed_first_party:
            source_type = SourceType.SOCIAL.value
        if (
            source_type == SourceType.OFFICIAL_SITE.value
            and not confirmed_first_party
        ):
            source_type = next(
                (
                    item
                    for item in preferred_source_types[1:]
                    if item != SourceType.OFFICIAL_SITE.value
                ),
                SourceType.OTHER.value,
            )
        source = SourceDocument(
            task_id=task_id,
            title=fetched.title or research_task.title,
            url=fetched.final_url,
            source_type=source_type,
            competitor=research_task.competitor,
            content_excerpt=fetched.text[:8000],
            reliability_score=reliability_score,
            metadata={
                "collection_method": collection_method,
                "research_task_id": research_task.id,
                "discovered_url": url,
                "search_result_id": search_result_id,
                "content_hash": fetched.content_hash,
                "render_mode": fetched.render_mode,
                "browser_engine": fetched.browser_engine,
                "first_party": confirmed_first_party,
                "source_level": (
                    str(search_result.metadata.get("source_level") or "")
                    if search_result
                    else (
                        "first_party" if confirmed_first_party else ""
                    )
                ),
                "official_confidence": (
                    str(
                        search_result.metadata.get("official_confidence")
                        or "unknown"
                    )
                    if search_result
                    else (
                        OfficialConfidence.CONFIRMED.value
                        if confirmed_first_party
                        else OfficialConfidence.UNKNOWN.value
                    )
                ),
                "published_at": (
                    search_result.published_at if search_result else ""
                ),
                "provider": search_result.provider if search_result else "",
                "tool_source_type": tool_source_type,
                "acquisition": {
                    "method": collection_method,
                    "search_attempt_id": (
                        search_result.search_attempt_id if search_result else ""
                    ),
                    "search_result_id": search_result_id,
                },
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
        self.store.save_many(task_id, "sources", [*existing_sources, source])
        self.store.save_many(task_id, "web_pages", [*pages, page])
        self.store.save_many(task_id, "collection_attempts", [*attempts, attempt])
        return {
            "status": "completed",
            "source_id": source.id,
            "web_page_id": page.id,
            "requested_url": url,
            "discovered_url": url,
            "final_url": source.url,
            "url": source.url,
            "title": source.title,
            "content_hash": page.content_hash,
            "text_chars": len(page.text),
            "render_mode": page.render_mode,
            "browser_engine": page.browser_engine,
            "collection_attempt_id": attempt.id,
            "reused": False,
        }

    def search_agent_query(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        query: str,
        limit: int,
        source_preference: str = "auto",
    ) -> tuple[ResearchTask, list[str]]:
        """Execute one Agent-owned query without creating policy fallback queries."""
        normalized_preference = str(
            source_preference or "auto"
        ).strip().casefold()
        if normalized_preference not in {"auto", "general", "community"}:
            normalized_preference = "auto"
        focused = research_task.model_copy(update={"query_hints": [query]})
        return self._discover_seed_urls(
            task_id=task_id,
            research_task=focused,
            limit=limit,
            acquisition_mode=normalized_preference,
            policy_query_only=True,
        )

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
                if self.search_transport is not None
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
        new_attempts: list[CollectionAttempt] = []

        for url in research_task.seed_urls[:limit]:
            payload = self.fetch_and_persist_url(
                task_id=task_id,
                research_task=research_task,
                url=url,
                search_result_id=search_result_by_url.get(url, ""),
                collection_method=(
                    "search_then_web_collector_v1"
                    if searched
                    else "web_collector_seed_url_v1"
                ),
                reliability_score=0.8 if not searched else 0.7,
            )
            source_id = str(payload.get("source_id") or "")
            if source_id:
                source = next(
                    (
                        SourceDocument(**item)
                        for item in self.store.load_many(task_id, "sources")
                        if item.get("id") == source_id
                    ),
                    None,
                )
                target = reused_sources if payload.get("reused") else new_sources
                if source is not None and all(item.id != source.id for item in target):
                    target.append(source)
            attempt_id = str(payload.get("collection_attempt_id") or "")
            if attempt_id:
                attempt = next(
                    (
                        CollectionAttempt(**item)
                        for item in self.store.load_many(
                            task_id,
                            "collection_attempts",
                        )
                        if item.get("id") == attempt_id
                    ),
                    None,
                )
                if attempt is not None:
                    new_attempts.append(attempt)

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
                output_refs.extend(
                    [
                        "search_attempts",
                        "web_search_results",
                        "source_selection_runs",
                    ]
                )
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
        acquisition_mode: str = "auto",
        policy_query_only: bool = False,
    ) -> tuple[ResearchTask, list[str]]:
        if self.search_transport is None:
            return research_task, []
        if acquisition_mode not in {"auto", "general", "community"}:
            raise ValueError("acquisition_mode 只支持 auto/general/community")
        attempts = [
            SearchAttempt(**item)
            for item in self.store.load_many(task_id, "search_attempts")
        ]
        results = [
            WebSearchResult(**item)
            for item in self.store.load_many(task_id, "web_search_results")
        ]
        selection_runs = [
            SourceSelectionRun(**item)
            for item in self.store.load_many(task_id, "source_selection_runs")
        ]
        existing_sources = [
            SourceDocument(**item)
            for item in self.store.load_many(task_id, "sources")
        ]
        evidence_coverage = [
            EvidenceCoverage(**item)
            for item in self.store.load_many(task_id, "evidence_coverage")
        ]
        selected_urls: list[str] = []
        selected_ids: list[str] = []
        current_results: list[WebSearchResult] = []
        current_attempt_ids: set[str] = set()
        confirmed_official_hosts = self._confirmed_official_domains(
            task_id=task_id,
            research_task=research_task,
        )
        probable_official_hosts_context = self._probable_official_domains(
            task_id=task_id,
            research_task=research_task,
        )
        rejected_official_hosts_context = self._rejected_official_domains(
            task_id=task_id,
            research_task=research_task,
        )
        tavily_official_resolution = (
            self.search_transport is not None
            and self.search_transport.provider_name.casefold() == "tavily"
        )
        recorder = TraceRecorder(store=self.store, task_id=task_id)
        recorder.tool_calls = [
            ToolCall(**item)
            for item in self.store.load_many(task_id, "tool_calls")
        ]

        def run_query(
            query: str,
            *,
            stage: str,
            domain_filter: str = "",
        ) -> tuple[SearchAttempt, list[WebSearchResult]]:
            attempt = SearchAttempt(
                task_id=task_id,
                research_task_id=research_task.id,
                provider=self.search_transport.provider_name,
                query=query,
                status="running",
                metadata={
                    "acquisition_stage": stage,
                    "discovered_official_hosts": [],
                    "selected_count": 0,
                },
            )
            query_results: list[WebSearchResult] = []
            requested_count = max(5, limit)
            started_at = utc_now()
            started_perf = time.perf_counter()
            try:
                hits = self.search_transport.search(
                    query,
                    count=requested_count,
                    domain_filter=domain_filter,
                )
                filtered_out = 0
                if domain_filter:
                    domain_hits = [
                        hit
                        for hit in hits
                        if _url_is_within_domain(hit.url, domain_filter)
                    ]
                    filtered_out = len(hits) - len(domain_hits)
                    hits = domain_hits
                provider_name = self.search_transport.provider_name
                completed_at = utc_now()
                duration_ms = int((time.perf_counter() - started_perf) * 1000)
                attempt = attempt.model_copy(
                    update={
                        "provider": provider_name,
                        "status": "completed",
                        "result_count": len(hits),
                        "metadata": {
                            **attempt.metadata,
                            "domain_filter": domain_filter,
                            "domain_filtered_out": filtered_out,
                        },
                    }
                )
                recorder.record_tool_call(
                    agent_run_id=f"collector_{research_task.id}",
                    tool_name="web_search",
                    input_data={
                        "task_id": task_id,
                        "research_task_id": research_task.id,
                        "query": query,
                        "limit": requested_count,
                        "domain_filter": domain_filter,
                        "transport": self.search_transport.transport,
                        "server": self.search_transport.server_name,
                        "provider": provider_name,
                    },
                    output_summary=(
                        f"transport={self.search_transport.transport}; "
                        f"server={self.search_transport.server_name or 'native'}; "
                        f"provider={provider_name}; results={len(hits)}"
                    ),
                    status=RunStatus.COMPLETED,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                )
                for rank, hit in enumerate(hits, start=1):
                    tool_metadata = dict(hit.metadata or {})
                    result = WebSearchResult(
                        task_id=task_id,
                        research_task_id=research_task.id,
                        search_attempt_id=attempt.id,
                        provider=provider_name,
                        query=query,
                        rank=rank,
                        title=hit.title,
                        url=hit.url,
                        snippet=hit.snippet,
                        site_name=hit.site_name,
                        published_at=hit.published_at,
                        metadata={
                            "acquisition_stage": stage,
                            "official_host_hint": domain_filter,
                            "source_type": hit.source_type,
                            "tool_metadata": tool_metadata,
                        },
                    )
                    results.append(result)
                    current_results.append(result)
                    query_results.append(result)
            except Exception as exc:
                completed_at = utc_now()
                duration_ms = int((time.perf_counter() - started_perf) * 1000)
                error = f"{type(exc).__name__}: {exc}"
                attempt = attempt.model_copy(
                    update={"status": "failed", "error": error}
                )
                recorder.record_tool_call(
                    agent_run_id=f"collector_{research_task.id}",
                    tool_name="web_search",
                    input_data={
                        "task_id": task_id,
                        "research_task_id": research_task.id,
                        "query": query,
                        "limit": requested_count,
                        "domain_filter": domain_filter,
                        "transport": self.search_transport.transport,
                        "server": self.search_transport.server_name,
                        "provider": self.search_transport.provider_name,
                    },
                    output_summary="",
                    status=RunStatus.FAILED,
                    error=error,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                )
            attempts.append(attempt)
            current_attempt_ids.add(attempt.id)
            return attempt, query_results

        def update_attempt_metadata(attempt_id: str, **values: object) -> None:
            for index, item in enumerate(attempts):
                if item.id != attempt_id:
                    continue
                attempts[index] = item.model_copy(
                    update={"metadata": {**item.metadata, **values}}
                )
                return

        def probable_official_hosts(
            candidates: list[WebSearchResult],
        ) -> list[str]:
            hosts: list[str] = []
            for candidate in sorted(candidates, key=lambda item: (item.rank, item.id)):
                try:
                    self.web_tool.url_policy.validate(candidate.url)
                except Exception:
                    continue
                for target in competitor_entities(research_task.competitor):
                    host = _probable_official_host(
                        candidate,
                        target=target,
                    )
                    if host and host not in hosts:
                        hosts.append(host)
                if len(hosts) >= 3:
                    break
            return hosts

        def coverage_state() -> str:
            matching = [
                item
                for item in evidence_coverage
                if item.competitor.casefold() == research_task.competitor.casefold()
                and canonical_dimension(item.dimension)
                == canonical_dimension(research_task.dimension)
            ]
            if matching:
                if matching[-1].status in {
                    EvidenceCoverageStatus.SUFFICIENT,
                    EvidenceCoverageStatus.NOT_APPLICABLE,
                }:
                    return "sufficient"
                return "insufficient"
            if research_task.research_gap_id or research_task.collection_round > 1:
                return "insufficient"
            return "unknown"

        def candidate_is_first_party(candidate_url: str, hosts: list[str]) -> bool:
            candidate_host = _normalized_official_host(candidate_url)
            return any(
                candidate_host == host or candidate_host.endswith(f".{host}")
                for host in hosts
            )

        def first_party_passes_specialized_quality(candidate) -> bool:
            """Let verified first-party ownership bypass only the generic score line."""

            if not candidate_is_first_party(
                candidate.result.url,
                confirmed_official_hosts,
            ):
                return False
            if candidate.relevance_score < 12.0:
                return False
            hard_rejection_markers = (
                "target_entity_missing",
                "generic_tutorial",
                "aggregator_navigation_or_seo",
                "third_party_tutorial",
                "repost_without_primary_provenance",
                "relevance_gate_short_target_context_not_corroborated",
                "relevance_gate_legal_policy_not_requested",
                "relevance_gate_official_domain_generic_tutorial_not_requested",
                "relevance_gate_aggregate_page_not_task_evidence",
            )
            return not any(
                marker in penalty
                for penalty in candidate.penalties
                for marker in hard_rejection_markers
            )

        def passes_collection_quality(candidate) -> bool:
            return (
                candidate.final_score >= MIN_COLLECTION_SCORE
                or first_party_passes_specialized_quality(candidate)
            )

        def qualified_first_party_url_count(hosts: list[str]) -> int:
            provisional_task = research_task.model_copy(
                update={
                    "metadata": {
                        **research_task.metadata,
                        "confirmed_official_domains": hosts,
                        "probable_official_domains": (
                            probable_official_hosts_context
                        ),
                        "rejected_official_domains": (
                            rejected_official_hosts_context
                        ),
                    }
                }
            )
            provisional = self.source_ranker.rank(
                current_results,
                provisional_task,
                existing_sources=existing_sources,
            )
            qualified_urls: set[str] = set()
            for candidate in provisional:
                if not passes_collection_quality(candidate):
                    continue
                if not candidate_is_first_party(candidate.result.url, hosts):
                    continue
                try:
                    self.web_tool.url_policy.validate(candidate.result.url)
                except Exception:
                    continue
                qualified_urls.add(candidate.result.url)
                if len(qualified_urls) >= limit:
                    break
            return len(qualified_urls)

        def qualified_url_count() -> int:
            provisional = self.source_ranker.rank(
                current_results,
                research_task,
                existing_sources=existing_sources,
            )
            qualified_urls: set[str] = set()
            for candidate in provisional:
                if not passes_collection_quality(candidate):
                    continue
                try:
                    self.web_tool.url_policy.validate(candidate.result.url)
                except Exception:
                    continue
                qualified_urls.add(candidate.result.url)
                if len(qualified_urls) >= limit:
                    break
            return len(qualified_urls)

        def merge_resolution(values: dict[str, list[str]]) -> None:
            for domain in values.get(OfficialConfidence.CONFIRMED.value, []):
                if domain not in confirmed_official_hosts:
                    confirmed_official_hosts.append(domain)
                if domain in probable_official_hosts_context:
                    probable_official_hosts_context.remove(domain)
                if domain in rejected_official_hosts_context:
                    rejected_official_hosts_context.remove(domain)
            for domain in values.get(OfficialConfidence.REJECTED.value, []):
                if domain in confirmed_official_hosts:
                    continue
                if domain not in rejected_official_hosts_context:
                    rejected_official_hosts_context.append(domain)
                if domain in probable_official_hosts_context:
                    probable_official_hosts_context.remove(domain)
            for domain in values.get(OfficialConfidence.PROBABLE.value, []):
                if (
                    domain not in confirmed_official_hosts
                    and domain not in rejected_official_hosts_context
                    and domain not in probable_official_hosts_context
                ):
                    probable_official_hosts_context.append(domain)

        def resolve_tavily_domains(method: str) -> None:
            if not (
                tavily_official_resolution
                and acquisition_mode == "auto"
                and _is_official_first_task(research_task)
            ):
                return
            merge_resolution(
                self._resolve_and_record_official_domains(
                    task_id=task_id,
                    research_task=research_task,
                    candidates=current_results,
                    verification_method=method,
                )
            )

        validated_domains: set[str] = set()

        def validate_probable_tavily_domains() -> None:
            if not tavily_official_resolution:
                return
            contexts = [
                OfficialDomainContext(**item)
                for item in self.store.load_many(
                    task_id,
                    "official_domain_contexts",
                )
            ]
            for domain in list(probable_official_hosts_context)[:3]:
                if (
                    domain in confirmed_official_hosts
                    or domain in rejected_official_hosts_context
                    or domain in validated_domains
                ):
                    continue
                context = next(
                    (
                        item
                        for item in reversed(contexts)
                        if item.domain == domain
                        and item.confidence == OfficialConfidence.PROBABLE.value
                        and competitor_context_matches(
                            item.competitor,
                            research_task.competitor,
                        )
                    ),
                    None,
                )
                if context is None:
                    continue
                validated_domains.add(domain)
                run_query(
                    (
                        f"{context.competitor} 官网 About 隐私政策 "
                        "开发者文档"
                    ),
                    stage="official_domain_validation",
                    domain_filter=domain,
                )
            if validated_domains:
                resolve_tavily_domains("tavily_domain_validation")

        discovery_queries = (
            _official_discovery_queries(research_task)
            if acquisition_mode == "auto"
            else []
        )
        for query in discovery_queries:
            attempt, query_results = run_query(
                query,
                stage="official_discovery",
            )
            discovered = probable_official_hosts(query_results)
            update_attempt_metadata(
                attempt.id,
                discovered_official_hosts=discovered,
            )
            for host in discovered:
                domain = self._official_context_domain(host)
                if domain and domain not in probable_official_hosts_context:
                    probable_official_hosts_context.append(domain)
                if len(probable_official_hosts_context) >= 3:
                    break
            if not tavily_official_resolution:
                self._record_official_domains(
                    task_id=task_id,
                    research_task=research_task,
                    domain_results={
                        domain: [
                            item.id
                            for item in query_results
                            if _url_is_within_domain(item.url, domain)
                        ]
                        for domain in discovered
                    },
                )

        resolve_tavily_domains("tavily_official_discovery")
        validate_probable_tavily_domains()

        targeted_domains: set[str] = set()
        if not policy_query_only:
            for host in confirmed_official_hosts[:3]:
                run_query(
                    _official_targeted_query(research_task, host),
                    stage="official_targeted",
                    domain_filter=host,
                )
                targeted_domains.add(host)

        current_coverage_state = coverage_state()
        general_search_reason = ""
        unresolved_official_entities = [
            item
            for item in competitor_entities(research_task.competitor)
            if item.casefold()
            not in self._confirmed_official_entities(
                task_id=task_id,
                research_task=research_task,
            )
        ]
        if (
            policy_query_only
            and acquisition_mode == "auto"
            and confirmed_official_hosts
            and not unresolved_official_entities
        ):
            for query in research_task.query_hints[:3]:
                for host in confirmed_official_hosts[:3]:
                    run_query(
                        query,
                        stage="research_agent_official_domain",
                        domain_filter=host,
                    )
                    targeted_domains.add(host)
        elif policy_query_only:
            general_search_reason = (
                "unresolved_competitor_official_domain"
                if unresolved_official_entities
                else "research_agent_policy_query"
            )
        elif acquisition_mode in {"general", "community"}:
            general_search_reason = f"research_agent_{acquisition_mode}"
        elif not _is_official_first_task(research_task):
            general_search_reason = "external_source_objective"
        elif not confirmed_official_hosts:
            general_search_reason = "no_confirmed_official_host"
        elif current_coverage_state == "insufficient":
            general_search_reason = "evidence_coverage_insufficient"
        elif (
            current_coverage_state == "unknown"
            and qualified_first_party_url_count(confirmed_official_hosts) == 0
        ):
            general_search_reason = "no_eligible_first_party_candidate"

        if general_search_reason:
            general_queries = (
                research_task.query_hints
                if policy_query_only
                else research_task.query_hints
                or [
                    f"{research_task.competitor} {research_task.dimension} 官方资料"
                ]
            )
            for query in general_queries[:3]:
                attempt, _query_results = run_query(
                    query,
                    stage="general",
                    domain_filter="",
                )
                update_attempt_metadata(
                    attempt.id,
                    general_search_reason=general_search_reason,
                    evidence_coverage_state=current_coverage_state,
                )
                if qualified_url_count() >= limit:
                    break
        if acquisition_mode == "auto" and _is_official_first_task(research_task):
            resolve_tavily_domains("tavily_general_discovery")
            validate_probable_tavily_domains()
            for host in confirmed_official_hosts[:3]:
                if host in targeted_domains:
                    continue
                queries = (
                    research_task.query_hints[:1]
                    if policy_query_only
                    else [_official_targeted_query(research_task, host)]
                )
                for query in queries:
                    run_query(
                        query,
                        stage=(
                            "research_agent_official_domain"
                            if policy_query_only
                            else "official_targeted"
                        ),
                        domain_filter=host,
                    )
                targeted_domains.add(host)

        if (
            not tavily_official_resolution
            and acquisition_mode == "auto"
            and _is_official_first_task(research_task)
        ):
            refreshed_hosts = probable_official_hosts(current_results)
            for host in refreshed_hosts:
                domain = self._official_context_domain(host)
                if domain and domain not in probable_official_hosts_context:
                    probable_official_hosts_context.append(domain)
                if len(probable_official_hosts_context) >= 3:
                    break
            domain_results = {
                domain: [
                    item.id
                    for item in current_results
                    if _url_is_within_domain(item.url, domain)
                ]
                for domain in probable_official_hosts_context
            }
            self._record_official_domains(
                task_id=task_id,
                research_task=research_task,
                domain_results=domain_results,
            )
        unique_current_results: list[WebSearchResult] = []
        seen_current_urls: set[str] = set()
        for item in current_results:
            if item.url in seen_current_urls:
                continue
            seen_current_urls.add(item.url)
            unique_current_results.append(item)
        current_results = unique_current_results

        ranking_task = research_task.model_copy(
            update={
                "metadata": {
                    **research_task.metadata,
                    "confirmed_official_domains": confirmed_official_hosts,
                    "probable_official_domains": probable_official_hosts_context,
                    "rejected_official_domains": rejected_official_hosts_context,
                }
            }
        )
        ranked = self.source_ranker.rank(
            current_results,
            ranking_task,
            existing_sources=existing_sources,
        )
        source_role_priority = {
            SourceRole.PRIMARY: 0,
            SourceRole.AUTHORITATIVE_SECONDARY: 1,
            SourceRole.GENERAL_THIRD_PARTY: 2,
            SourceRole.COMMUNITY: 3,
            SourceRole.LOW_QUALITY: 4,
        }
        if acquisition_mode == "community":
            source_role_priority[SourceRole.COMMUNITY] = 1
            source_role_priority[SourceRole.AUTHORITATIVE_SECONDARY] = 2
            source_role_priority[SourceRole.GENERAL_THIRD_PARTY] = 3
        ranked = sorted(
            ranked,
            key=lambda candidate: (
                0
                if candidate_is_first_party(
                    candidate.result.url,
                    confirmed_official_hosts,
                )
                else 1,
                source_role_priority[candidate.source_role],
                candidate.quality_rank,
            ),
        )
        final_confirmed_entities = self._confirmed_official_entities(
            task_id=task_id,
            research_task=research_task,
        )
        final_unresolved_entities = [
            item
            for item in competitor_entities(research_task.competitor)
            if item.casefold() not in final_confirmed_entities
        ]
        strict_first_party_available = bool(
            acquisition_mode == "auto"
            and _is_official_first_task(research_task)
            and confirmed_official_hosts
            and current_coverage_state != "insufficient"
            and not final_unresolved_entities
            and qualified_first_party_url_count(confirmed_official_hosts) > 0
        )
        result_updates: dict[str, WebSearchResult] = {}
        new_selection_runs: list[SourceSelectionRun] = []
        for candidate in ranked:
            selected = False
            rejection = ""
            first_party_quality_exception = False
            try:
                self.web_tool.url_policy.validate(candidate.result.url)
                first_party_quality_exception = (
                    candidate.final_score < MIN_COLLECTION_SCORE
                    and first_party_passes_specialized_quality(candidate)
                )
                if not passes_collection_quality(candidate):
                    rejection = (
                        "quality_below_minimum:"
                        f"{candidate.final_score:g}<{MIN_COLLECTION_SCORE:g}"
                    )
                elif (
                    strict_first_party_available
                    and not candidate_is_first_party(
                        candidate.result.url,
                        confirmed_official_hosts,
                    )
                ):
                    rejection = "deferred_until_evidence_coverage_fallback"
                elif candidate.result.url in selected_urls:
                    rejection = "duplicate_url"
                elif len(selected_urls) >= limit:
                    rejection = "source_budget_reached"
                else:
                    selected = True
            except Exception as exc:
                rejection = f"unsafe_url: {exc}"
            selection_reason = (
                (
                    "selected_first_party_after_specialized_quality_safety_and_budget"
                    if first_party_quality_exception
                    else "selected_after_quality_rank_safety_and_budget"
                )
                if selected
                else rejection
            )
            updated = candidate.result.model_copy(
                update={
                    "selected_for_collection": selected,
                    "rejection_reason": rejection,
                    "metadata": {
                        **candidate.result.metadata,
                        "domain": candidate.domain,
                        "first_party": candidate_is_first_party(
                            candidate.result.url,
                            confirmed_official_hosts,
                        ),
                        "source_role": candidate.source_role.value,
                        "official_confidence": (
                            candidate.official_confidence.value
                        ),
                        "source_level": {
                            SourceRole.PRIMARY: "first_party",
                            SourceRole.AUTHORITATIVE_SECONDARY: (
                                "authoritative_third_party"
                            ),
                            SourceRole.GENERAL_THIRD_PARTY: (
                                "general_third_party"
                            ),
                            SourceRole.COMMUNITY: "community",
                            SourceRole.LOW_QUALITY: "low_quality",
                        }[candidate.source_role],
                    },
                }
            )
            result_updates[updated.id] = updated
            new_selection_runs.append(
                candidate.to_artifact(
                    selected=selected,
                    selection_reason=selection_reason,
                )
            )
            if selected:
                selected_urls.append(updated.url)
                selected_ids.append(updated.id)

        selected_by_attempt: dict[str, int] = {}
        for result in result_updates.values():
            if result.selected_for_collection:
                selected_by_attempt[result.search_attempt_id] = (
                    selected_by_attempt.get(result.search_attempt_id, 0) + 1
                )
        for attempt_id in current_attempt_ids:
            update_attempt_metadata(
                attempt_id,
                selected_count=selected_by_attempt.get(attempt_id, 0),
            )

        if result_updates:
            results = [result_updates.get(item.id, item) for item in results]

        self.store.save_many(task_id, "search_attempts", attempts)
        self.store.save_many(task_id, "web_search_results", results)
        self.store.save_many(
            task_id,
            "source_selection_runs",
            selection_runs + new_selection_runs,
        )
        return research_task.model_copy(update={"seed_urls": selected_urls}), selected_ids
