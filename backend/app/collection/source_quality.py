from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.schemas import (
    OfficialConfidence,
    ResearchTask,
    SourceDocument,
    SourceRole,
    SourceSelectionRun,
    WebSearchResult,
)


RANKING_VERSION = "source_quality_v1_fix2"
MIN_COLLECTION_SCORE = 20.0
RELEVANCE_GATE_SCORE_CAP = MIN_COLLECTION_SCORE - 0.01
RELEVANCE_GATE_SCORE_MULTIPLIER = 0.6
_COMPOUND_SUFFIXES = {
    "com.cn",
    "net.cn",
    "org.cn",
    "gov.cn",
    "co.uk",
    "org.uk",
    "com.au",
    "com.sg",
    "co.jp",
}
_GENERIC_TERMS = {
    "官方",
    "文档",
    "资料",
    "信息",
    "产品",
    "表现",
    "可靠",
    "采集",
    "核实",
    "回答",
    "方面",
    "about",
    "official",
    "documentation",
    "docs",
    "product",
    "information",
}
_GENERIC_TUTORIAL_PATTERNS = (
    "通用教程",
    "产品需求分析",
    "产品定位是什么",
    "产品定位方法",
    "市场导向法",
    "价值导向法",
    "成本分析",
    "定价方法",
    "定价策略",
    "模板范文",
    "方法论",
    "怎么写",
    "入门教程",
    "完整教程",
    "step by step",
    "how to price",
    "pricing strategy template",
    "product positioning template",
)
_AGGREGATE_PATTERNS = (
    "工具导航",
    "ai导航",
    "网站导航",
    "网址导航",
    "资源导航",
    "排行榜",
    "网址大全",
    "工具大全",
    "合集",
    "聚合",
    "收录",
    "top 10",
    "best tools",
)
_SEO_TUTORIAL_PATTERNS = (
    "怎么做",
    "怎么用",
    "怎么用于",
    "如何使用",
    "如何配置",
    "使用指南",
    "操作指南",
    "实现指南",
    "深度解析",
    "实战案例",
    "实战教程",
    "完整教程",
    "一文看懂",
    "常见问题",
    " faq",
    "/faq/",
    "how to use",
    "complete guide",
)
_STRONG_SEO_TITLE_PATTERNS = (
    "从零开始",
    "手把手",
)
_LEGAL_POLICY_PATTERNS = (
    "terms of service",
    "terms-of-service",
    "privacy policy",
    "privacy-policy",
    "服务条款",
    "隐私政策",
    "用户协议",
    "法律声明",
)
_LEGAL_TASK_MARKERS = (
    "条款",
    "隐私",
    "协议",
    "合规",
    "法律",
    "terms",
    "privacy",
    "policy",
    "compliance",
)
_TUTORIAL_TASK_MARKERS = (
    "教程",
    "指南",
    "用法",
    "如何使用",
    "如何配置",
    "实战",
    "how to",
    "tutorial",
    "guide",
    "faq",
)
_COMMUNITY_HOST_MARKERS = (
    "forum.",
    "community.",
    "discuss.",
    "reddit.",
    "stackoverflow.",
    "zhihu.",
    "csdn.",
    "github.com",
    "gitee.com",
)
_COMMUNITY_TEXT_MARKERS = (
    "社区",
    "论坛",
    "用户反馈",
    "使用体验",
    "实测",
    "踩坑",
    "开发者反馈",
    "user review",
    "hands-on",
)
_EXPERIENCE_OBJECTIVE_MARKERS = (
    "用户",
    "体验",
    "实测",
    "踩坑",
    "反馈",
    "社区",
    "案例",
    "实际使用",
    "故障",
    "bug",
    "review",
    "experience",
)
_SECONDARY_MARKERS = (
    "新闻",
    "科技",
    "财经",
    "研究",
    "报告",
    "测评",
    "评测",
    "媒体",
    "报道",
    "记者",
    "编辑",
    "news",
    "technology",
    "research",
    "report",
    "analysis",
)
_OFFICIAL_SURFACE_MARKERS = (
    "docs",
    "doc",
    "help",
    "support",
    "pricing",
    "billing",
    "changelog",
    "release",
    "api",
    "marketplace",
    "integration",
    "integrations",
    "about",
    "product",
    "feature",
    "blog",
    "terms",
    "developer",
    "developers",
)
_DIMENSION_KEYWORDS = {
    "positioning": (
        "定位",
        "面向",
        "目标用户",
        "适用场景",
        "价值主张",
        "about",
        "overview",
    ),
    "feature": (
        "功能",
        "能力",
        "特性",
        "使用指南",
        "文档",
        "docs",
        "feature",
        "changelog",
        "release",
        "help",
    ),
    "pricing": (
        "价格",
        "定价",
        "费用",
        "订阅",
        "套餐",
        "计费",
        "pricing",
        "billing",
        "subscription",
        "plan",
    ),
    "ecosystem": (
        "生态",
        "第三方集成",
        "工具集成",
        "外部集成",
        "集成市场",
        "集成能力",
        "插件",
        "市场",
        "合作伙伴",
        "mcp",
        "api",
        "integration",
        "marketplace",
        "plugin",
        "repository",
    ),
    "other": (),
}
_STRONG_DIMENSION_KEYWORDS = {
    "positioning": (
        "定位",
        "面向",
        "目标用户",
        "适用场景",
        "价值主张",
        "positioning",
        "target user",
        "use case",
        "about",
        "overview",
    ),
    "feature": (
        "功能",
        "能力",
        "特性",
        "代码补全",
        "智能体",
        "agent",
        "skill",
        "skills",
        "debug",
        "debugging",
        "feature",
        "capability",
        "changelog",
        "release",
    ),
    "pricing": (
        "价格",
        "定价",
        "费用",
        "订阅",
        "套餐",
        "计费",
        "pricing",
        "billing",
        "subscription",
        "price",
    ),
    "ecosystem": (
        "生态",
        "第三方集成",
        "工具集成",
        "外部集成",
        "集成市场",
        "集成能力",
        "插件",
        "合作伙伴",
        "mcp",
        "api",
        "integration",
        "marketplace",
        "plugin",
        "repository",
    ),
    "other": (),
}
_SHORT_TARGET_CONTEXT_KEYWORDS = {
    "positioning": _STRONG_DIMENSION_KEYWORDS["positioning"],
    "feature": _STRONG_DIMENSION_KEYWORDS["feature"]
    + ("产品", "安全", "登录", "消息"),
    "pricing": _STRONG_DIMENSION_KEYWORDS["pricing"]
    + ("会员", "付费", "充值", "费率"),
    "ecosystem": _STRONG_DIMENSION_KEYWORDS["ecosystem"]
    + ("集成", "登录", "分享", "开放平台", "开发者"),
    "other": (),
}


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def _contains_marker(value: str, marker: str) -> bool:
    normalized = _normalized(value)
    normalized_marker = _normalized(marker)
    if re.search(r"[a-z0-9]", normalized_marker):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_marker)}(?![a-z0-9])",
                normalized,
            )
        )
    return normalized_marker in normalized


def _compact(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", _normalized(value)))


def tokenize(value: str) -> list[str]:
    normalized = _normalized(value)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def canonical_dimension(value: str) -> str:
    normalized = _normalized(value)
    # Match specific dimensions before broad words such as ``能力``.  For
    # example, ``生态与集成能力`` is ecosystem, not feature.
    if any(
        marker in normalized
        for marker in ("生态", "集成", "ecosystem", "integration")
    ):
        return "ecosystem"
    if any(
        marker in normalized
        for marker in (
            "定价",
            "价格",
            "收费",
            "付费",
            "消费模式",
            "计费",
            "成本",
            "pricing",
            "price",
            "billing",
            "cost",
            "monetization",
        )
    ):
        return "pricing"
    if "定位" in normalized or "position" in normalized:
        return "positioning"
    if any(
        marker in normalized
        for marker in ("功能", "能力", "feature", "capabil")
    ):
        return "feature"
    if any(marker in normalized for marker in ("客户", "用户", "customer")):
        return "customer"
    if any(
        marker in normalized
        for marker in ("风险", "安全", "合规", "部署", "责任", "risk")
    ):
        return "risk"
    if normalized == "other" or "其他" in normalized:
        return "other"
    return "other"


def hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def registrable_domain(value: str) -> str:
    host = hostname(value) if "://" in value else value.casefold().strip(". ")
    if host.startswith("www."):
        host = host[4:]
    labels = [item for item in host.split(".") if item]
    if len(labels) <= 2:
        return ".".join(labels)
    suffix = ".".join(labels[-2:])
    if suffix in _COMPOUND_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _domain_matches(candidate: str, confirmed: str) -> bool:
    candidate_host = hostname(candidate) if "://" in candidate else candidate
    confirmed_host = hostname(confirmed) if "://" in confirmed else confirmed
    candidate_host = candidate_host.casefold().strip(". ")
    confirmed_host = confirmed_host.casefold().strip(". ")
    return bool(
        candidate_host
        and confirmed_host
        and (
            candidate_host == confirmed_host
            or candidate_host.endswith(f".{confirmed_host}")
            or registrable_domain(candidate_host) == registrable_domain(confirmed_host)
        )
    )


def _target_forms(competitor: str) -> list[str]:
    normalized = _normalized(competitor)
    forms = {_compact(normalized)}
    generic = {"产品", "方案", "服务", "平台", "系统", "教育", "工具"}
    for part in re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", normalized):
        compact = _compact(part)
        if compact and compact not in generic:
            forms.add(compact)
    valid = (
        item
        for item in forms
        if (
            len(item) >= 3
            or (len(item) >= 2 and bool(re.fullmatch(r"[a-z0-9]+", item)))
            or (len(item) >= 2 and bool(re.fullmatch(r"[\u4e00-\u9fff]+", item)))
        )
    )
    return sorted(valid, key=len, reverse=True)


def _is_short_target_form(form: str) -> bool:
    return len(form) == 2 and bool(
        re.fullmatch(r"[a-z0-9]+|[\u4e00-\u9fff]+", form)
    )


def _contains_target(value: str, forms: list[str]) -> bool:
    normalized = _normalized(value)
    compact = _compact(value)
    for form in forms:
        if _is_short_target_form(form) and re.fullmatch(r"[a-z0-9]+", form):
            if re.search(
                rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])",
                normalized,
            ):
                return True
        elif form in compact:
            return True
    return False


def _short_target_near_task_signal(
    value: str,
    forms: list[str],
    markers: tuple[str, ...],
    *,
    window: int = 24,
) -> bool:
    if not markers:
        return False
    normalized = _normalized(value)
    for form in (item for item in forms if _is_short_target_form(item)):
        ascii_form = bool(re.fullmatch(r"[a-z0-9]+", form))
        pattern = (
            rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])"
            if ascii_form
            else re.escape(form)
        )
        for match in re.finditer(pattern, normalized):
            # A short ASCII token surrounded by CJK characters may be part of a
            # different named entity (for example a model name).  It still
            # counts when the task signal is immediately adjacent, as in
            # ``AB产品能力`` or ``使用AB登录``; a wider contextual hit is not
            # enough to disambiguate it.
            previous = normalized[match.start() - 1 : match.start()]
            following = normalized[match.end() : match.end() + 1]
            embedded_in_cjk = (
                ascii_form
                and bool(re.fullmatch(r"[\u4e00-\u9fff]", previous))
                and bool(re.fullmatch(r"[\u4e00-\u9fff]", following))
            )
            effective_window = min(window, 8) if embedded_in_cjk else window
            context = normalized[
                max(0, match.start() - effective_window) :
                min(len(normalized), match.end() + effective_window)
            ]
            if any(_contains_marker(context, marker) for marker in markers):
                return True
    return False


def _metadata_domains(research_task: ResearchTask, key: str) -> list[str]:
    raw = research_task.metadata.get(key, [])
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _confirmed_domains(
    research_task: ResearchTask,
    existing_sources: list[SourceDocument],
) -> set[str]:
    domains = {
        registrable_domain(item)
        for item in (
            _metadata_domains(research_task, "official_domains")
            + _metadata_domains(research_task, "confirmed_official_domains")
        )
        if registrable_domain(item)
    }
    for source in existing_sources:
        if source.competitor.casefold() != research_task.competitor.casefold():
            continue
        confidence = str(source.metadata.get("official_confidence") or "")
        explicitly_confirmed = (
            confidence == OfficialConfidence.CONFIRMED.value
            or source.metadata.get("official_domain_confirmed") is True
            or source.metadata.get("is_official") is True
        )
        if explicitly_confirmed:
            domain = registrable_domain(source.url)
            if domain:
                domains.add(domain)
    return domains


def _probable_domains(research_task: ResearchTask) -> set[str]:
    return {
        registrable_domain(item)
        for item in (
            list(research_task.preferred_domains)
            + _metadata_domains(research_task, "probable_official_domains")
        )
        if registrable_domain(item)
    }


def _rejected_domains(research_task: ResearchTask) -> set[str]:
    return {
        registrable_domain(item)
        for item in _metadata_domains(research_task, "rejected_official_domains")
        if registrable_domain(item)
    }


def _official_confidence(
    result: WebSearchResult,
    research_task: ResearchTask,
    confirmed_domains: set[str],
    probable_domains: set[str],
    rejected_domains: set[str],
) -> OfficialConfidence:
    host = hostname(result.url)
    registered = registrable_domain(host)
    if any(_domain_matches(host, item) for item in confirmed_domains):
        return OfficialConfidence.CONFIRMED
    if any(_domain_matches(host, item) for item in rejected_domains):
        return OfficialConfidence.REJECTED
    if any(_domain_matches(host, item) for item in probable_domains):
        return OfficialConfidence.PROBABLE
    forms = _target_forms(research_task.competitor)
    short_target = any(_is_short_target_form(item) for item in forms)
    main_label = registered.split(".")[0] if registered else ""
    latin_brand_tokens = {
        item
        for item in forms
        if re.fullmatch(r"[a-z0-9]{2,}", item)
    }
    candidate_identity = " ".join(
        (result.title, result.site_name, result.snippet[:500])
    )
    identity_has_target = _contains_target(candidate_identity, forms)
    site_name_has_target = _contains_target(result.site_name, forms)
    url_surface = f"{host}/{urlparse(result.url).path}"
    documentation_host = any(
        label in {"docs", "doc", "developer", "developers", "help", "support", "api"}
        for label in host.split(".")
    )
    documentation_path = any(
        _contains_marker(urlparse(result.url).path, marker)
        for marker in ("docs", "doc", "help", "support", "api")
    )
    path_segments = [
        item for item in urlparse(result.url).path.strip("/").split("/") if item
    ]
    path_root_has_target = bool(
        path_segments and _contains_target(path_segments[0], forms)
    )
    if (
        main_label in latin_brand_tokens
        and identity_has_target
    ):
        return OfficialConfidence.PROBABLE
    if (
        short_target
        and identity_has_target
        and (
            (documentation_host and (site_name_has_target or path_root_has_target))
            or (site_name_has_target and documentation_path)
        )
    ):
        return OfficialConfidence.PROBABLE
    return OfficialConfidence.UNKNOWN


def _signals(
    result: WebSearchResult,
    research_task: ResearchTask,
) -> dict[str, bool]:
    host = hostname(result.url)
    parsed = urlparse(result.url)
    path = _normalized(parsed.path)
    text = _normalized(" ".join((result.title, result.site_name, result.snippet)))
    surface_text = _normalized(" ".join((result.title, result.site_name, result.url)))
    task_text = _normalized(
        " ".join(
            (
                research_task.objective,
                research_task.dimension,
                " ".join(research_task.query_hints),
            )
        )
    )
    forms = _target_forms(research_task.competitor)
    target_title_site = _contains_target(
        " ".join((result.title, result.site_name)), forms
    )
    target_url = _contains_target(f"{host} {parsed.path}", forms)
    target_primary = target_title_site or target_url
    target_snippet = _contains_target(result.snippet, forms)
    target_present = target_primary or target_snippet
    generic_tutorial = any(marker in text for marker in _GENERIC_TUTORIAL_PATTERNS)
    aggregate = (
        any(marker in text for marker in _AGGREGATE_PATTERNS)
        or bool(re.search(r"/(?:tags?|category|catalog|rankings?)(?:/|$)", path))
    )
    seo_tutorial = any(
        marker in f"{surface_text} {text[:1000]}"
        for marker in _SEO_TUTORIAL_PATTERNS
    ) or any(
        marker in _normalized(result.title)
        for marker in _STRONG_SEO_TITLE_PATTERNS
    )
    legal_policy = any(marker in surface_text for marker in _LEGAL_POLICY_PATTERNS)
    task_asks_legal = any(marker in task_text for marker in _LEGAL_TASK_MARKERS)
    task_asks_tutorial = any(marker in task_text for marker in _TUTORIAL_TASK_MARKERS)
    secondary = any(marker in text for marker in _SECONDARY_MARKERS)
    community_host = any(marker in host for marker in _COMMUNITY_HOST_MARKERS)
    community_text = any(marker in text for marker in _COMMUNITY_TEXT_MARKERS)
    community = community_host or (community_text and not secondary)
    experience_objective = any(
        marker in _normalized(research_task.objective)
        for marker in _EXPERIENCE_OBJECTIVE_MARKERS
    )
    repost = any(marker in text for marker in ("转载", "转自", "原文来源", "reposted"))
    official_surface = not path.strip("/") or any(
        _contains_marker(f"{host}/{path}", marker)
        for marker in _OFFICIAL_SURFACE_MARKERS
    )
    documentation_surface = any(
        _contains_marker(f"{host}/{path}", marker)
        for marker in ("docs", "doc", "developer", "developers", "help", "support", "api")
    )
    dimension = canonical_dimension(research_task.dimension)
    strong_dimension_match = dimension == "other" or any(
        _contains_marker(" ".join((result.title, result.snippet, result.url)), marker)
        for marker in _STRONG_DIMENSION_KEYWORDS.get(dimension, ())
    )
    if dimension == "feature" and documentation_surface and target_present:
        strong_dimension_match = True
    short_target = any(_is_short_target_form(item) for item in forms)
    short_target_task_match = _short_target_near_task_signal(
        " ".join((result.title, result.snippet)),
        forms,
        _SHORT_TARGET_CONTEXT_KEYWORDS.get(dimension, ()),
    )
    short_target_corroborated = not short_target or (
        target_present
        and (
            (target_url and (target_title_site or target_snippet))
            or short_target_task_match
            or (
                documentation_surface
                and target_title_site
                and target_snippet
            )
        )
    )
    return {
        "short_target": short_target,
        "short_target_corroborated": short_target_corroborated,
        "short_target_task_match": short_target_task_match,
        "target_title_site": target_title_site,
        "target_url": target_url,
        "target_primary": target_primary,
        "target_snippet": target_snippet,
        "target_present": target_present,
        "generic_tutorial": generic_tutorial,
        "aggregate": aggregate,
        "seo_tutorial": seo_tutorial,
        "legal_policy": legal_policy,
        "task_asks_legal": task_asks_legal,
        "task_asks_tutorial": task_asks_tutorial,
        "community": community,
        "community_experience": community_text,
        "experience_objective": experience_objective,
        "secondary": secondary,
        "repost": repost,
        "official_surface": official_surface,
        "strong_dimension_match": strong_dimension_match,
    }


def _source_role(
    confidence: OfficialConfidence,
    signals: dict[str, bool],
) -> SourceRole:
    if signals["generic_tutorial"] and not signals["target_present"]:
        return SourceRole.LOW_QUALITY
    if (
        signals["seo_tutorial"]
        and confidence != OfficialConfidence.CONFIRMED
        and not signals["experience_objective"]
    ):
        return SourceRole.LOW_QUALITY
    if signals["community"] and not signals["target_present"]:
        return SourceRole.LOW_QUALITY
    if signals["community"] and signals["community_experience"]:
        return SourceRole.COMMUNITY
    if confidence == OfficialConfidence.CONFIRMED and signals["target_present"]:
        return SourceRole.PRIMARY
    if signals["community"]:
        return SourceRole.COMMUNITY
    if signals["aggregate"] or (
        signals["seo_tutorial"] and not signals["secondary"]
    ):
        return SourceRole.LOW_QUALITY
    if signals["secondary"]:
        return SourceRole.AUTHORITATIVE_SECONDARY
    if signals["target_present"]:
        return SourceRole.GENERAL_THIRD_PARTY
    return SourceRole.LOW_QUALITY


def _relevance_score(
    result: WebSearchResult,
    research_task: ResearchTask,
    confidence: OfficialConfidence,
    signals: dict[str, bool],
) -> float:
    score = 0.0
    if signals["target_primary"]:
        score += 24.0
    elif signals["target_snippet"]:
        score += 18.0
    elif confidence == OfficialConfidence.CONFIRMED:
        score += 20.0

    expected = Counter(
        token
        for token in tokenize(
            " ".join(
                (
                    research_task.objective,
                    research_task.dimension,
                    result.query,
                    " ".join(research_task.query_hints),
                )
            )
        )
        if token not in _GENERIC_TERMS
    )
    candidate_tokens = set(
        tokenize(" ".join((result.title, result.snippet, result.site_name, result.url)))
    )
    overlap = sum(1 for token in expected if token in candidate_tokens)
    if expected:
        score += min(10.0, overlap / min(len(expected), 12) * 10.0)

    dimension = canonical_dimension(research_task.dimension)
    dimension_text = " ".join((result.title, result.snippet, result.url))
    dimension_hits = sum(
        _contains_marker(dimension_text, marker)
        for marker in _DIMENSION_KEYWORDS.get(dimension, ())
    )
    score += 6.0 if dimension_hits >= 2 else (4.0 if dimension_hits == 1 else 0.0)
    return round(min(score, 40.0), 2)


def _dimension_fit_score(
    dimension: str,
    role: SourceRole,
    confidence: OfficialConfidence,
    relevance: float,
    signals: dict[str, bool],
    result: WebSearchResult,
) -> float:
    if signals["generic_tutorial"] and not signals["target_present"]:
        return 0.0
    surface = _normalized(f"{hostname(result.url)} {urlparse(result.url).path} {result.title}")
    official = role == SourceRole.PRIMARY
    community_bonus = signals["experience_objective"]
    if dimension == "positioning":
        if official and any(
            _contains_marker(surface, item) for item in ("about", "product", "overview")
        ):
            return 25.0
        return {
            SourceRole.PRIMARY: 22.0,
            SourceRole.AUTHORITATIVE_SECONDARY: 16.0,
            SourceRole.GENERAL_THIRD_PARTY: 11.0,
            SourceRole.COMMUNITY: 10.0 if community_bonus else 7.0,
            SourceRole.LOW_QUALITY: 2.0,
        }[role]
    if dimension == "feature":
        if official and any(
            _contains_marker(surface, item)
            for item in ("docs", "help", "changelog", "feature", "release")
        ):
            return 25.0
        return {
            SourceRole.PRIMARY: 21.0,
            SourceRole.AUTHORITATIVE_SECONDARY: 16.0,
            SourceRole.GENERAL_THIRD_PARTY: 12.0,
            SourceRole.COMMUNITY: 15.0 if community_bonus else 11.0,
            SourceRole.LOW_QUALITY: 2.0,
        }[role]
    if dimension == "pricing":
        if official and any(
            _contains_marker(surface, item)
            for item in ("pricing", "billing", "subscription", "plan", "faq", "terms")
        ):
            return 25.0
        return {
            SourceRole.PRIMARY: 20.0,
            SourceRole.AUTHORITATIVE_SECONDARY: 16.0,
            SourceRole.GENERAL_THIRD_PARTY: 10.0,
            SourceRole.COMMUNITY: 13.0 if community_bonus else 8.0,
            SourceRole.LOW_QUALITY: 1.0,
        }[role]
    if dimension == "ecosystem":
        if official and any(
            _contains_marker(surface, item)
            for item in ("docs", "integration", "marketplace", "api", "plugin")
        ):
            return 25.0
        return {
            SourceRole.PRIMARY: 20.0,
            SourceRole.AUTHORITATIVE_SECONDARY: 17.0,
            SourceRole.GENERAL_THIRD_PARTY: 12.0,
            SourceRole.COMMUNITY: 15.0 if community_bonus else 12.0,
            SourceRole.LOW_QUALITY: 2.0,
        }[role]
    base = min(25.0, 5.0 + relevance * 0.5)
    if role == SourceRole.COMMUNITY and community_bonus:
        base = max(base, 20.0)
    if role == SourceRole.LOW_QUALITY:
        base = min(base, 5.0)
    if role == SourceRole.PRIMARY and not urlparse(result.url).path.strip("/"):
        base = max(base, 22.0)
    if role == SourceRole.PRIMARY and any(
        _contains_marker(surface, item)
        for item in ("docs", "help", "product", "feature", "changelog", "release")
    ):
        base = max(base, 20.0)
    if confidence == OfficialConfidence.CONFIRMED:
        base = max(base, 20.0)
    return round(base, 2)


def _authority_score(role: SourceRole, experience_objective: bool) -> float:
    if role == SourceRole.PRIMARY:
        return 25.0
    if role == SourceRole.AUTHORITATIVE_SECONDARY:
        return 18.0
    if role == SourceRole.GENERAL_THIRD_PARTY:
        return 10.0
    if role == SourceRole.COMMUNITY:
        return 20.0 if experience_objective else 12.0
    return 2.0


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", value)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _freshness_score(result: WebSearchResult, dimension: str) -> float:
    published = _parse_date(result.published_at) or _parse_date(result.title)
    if published is None:
        return 0.0
    reference = result.created_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_days = max(0, (reference - published).days)
    time_sensitive = dimension in {"feature", "pricing", "ecosystem"}
    if age_days <= 365:
        return 10.0 if time_sensitive else 7.0
    if age_days <= 730:
        return 7.0 if time_sensitive else 5.0
    if age_days <= 1460:
        return 4.0 if time_sensitive else 3.0
    return 1.0


def _penalties(
    relevance: float,
    confidence: OfficialConfidence,
    signals: dict[str, bool],
    research_task: ResearchTask,
    result: WebSearchResult,
) -> list[tuple[str, float]]:
    values: list[tuple[str, float]] = []
    associated_domains = _metadata_domains(research_task, "associated_domains")
    reasonable_association = (
        confidence == OfficialConfidence.CONFIRMED
        or any(_domain_matches(hostname(result.url), item) for item in associated_domains)
    )
    if not signals["target_present"] and not reasonable_association:
        values.append(("target_entity_missing", -36.0))
    if signals["generic_tutorial"] and not signals["target_present"]:
        values.append(("generic_tutorial_or_template", -30.0))
    if signals["aggregate"] and not (
        confidence == OfficialConfidence.CONFIRMED and signals["official_surface"]
    ):
        values.append(("aggregator_navigation_or_seo", -22.0))
    if (
        signals["seo_tutorial"]
        and confidence != OfficialConfidence.CONFIRMED
        and not signals["experience_objective"]
    ):
        values.append(("third_party_tutorial_without_primary_provenance", -25.0))
    if signals["repost"] and confidence != OfficialConfidence.CONFIRMED:
        values.append(("repost_without_primary_provenance", -15.0))
    if relevance < 12.0:
        values.append(("objective_relevance_very_weak", -15.0))
    elif relevance < 20.0:
        values.append(("objective_relevance_weak", -8.0))
    return values


def _relevance_gate_reason(
    dimension: str,
    confidence: OfficialConfidence,
    signals: dict[str, bool],
) -> str:
    """Return a deterministic reason when authority must not rescue weak task fit."""
    if (
        signals["short_target"]
        and signals["target_present"]
        and not signals["short_target_corroborated"]
    ):
        return "short_target_context_not_corroborated"
    if signals["legal_policy"] and not signals["task_asks_legal"]:
        return "legal_policy_not_requested"
    if dimension != "other" and not signals["strong_dimension_match"]:
        return "research_dimension_not_matched"
    if (
        signals["seo_tutorial"]
        and not signals["task_asks_tutorial"]
        and not signals["experience_objective"]
    ):
        if confidence != OfficialConfidence.CONFIRMED:
            return "third_party_tutorial_not_requested"
        if not signals["official_surface"]:
            return "official_domain_generic_tutorial_not_requested"
    if signals["aggregate"] and not (
        confidence == OfficialConfidence.CONFIRMED and signals["official_surface"]
    ):
        return "aggregate_page_not_task_evidence"
    return ""


@dataclass(frozen=True)
class RankedSourceCandidate:
    result: WebSearchResult
    domain: str
    source_role: SourceRole
    official_confidence: OfficialConfidence
    relevance_score: float
    dimension_fit_score: float
    authority_score: float
    freshness_score: float
    penalties: tuple[str, ...]
    penalty_score: float
    final_score: float
    diversity_penalty: float = 0.0
    quality_rank: int = 0

    @property
    def adjusted_score(self) -> float:
        return self.final_score - self.diversity_penalty

    def to_artifact(self, *, selected: bool, selection_reason: str) -> SourceSelectionRun:
        identity = "|".join(
            (
                self.result.task_id,
                self.result.research_task_id,
                self.result.id,
                RANKING_VERSION,
            )
        )
        artifact_id = f"sourceselection_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"
        return SourceSelectionRun(
            id=artifact_id,
            task_id=self.result.task_id,
            research_task_id=self.result.research_task_id,
            search_attempt_id=self.result.search_attempt_id,
            search_result_id=self.result.id,
            query=self.result.query,
            url=self.result.url,
            domain=self.domain,
            source_role=self.source_role,
            official_confidence=self.official_confidence,
            relevance_score=self.relevance_score,
            dimension_fit_score=self.dimension_fit_score,
            authority_score=self.authority_score,
            freshness_score=self.freshness_score,
            penalties=list(self.penalties),
            penalty_score=self.penalty_score,
            diversity_penalty=self.diversity_penalty,
            final_score=self.final_score,
            quality_rank=self.quality_rank,
            selected=selected,
            selection_reason=selection_reason,
            ranking_version=RANKING_VERSION,
            created_at=self.result.created_at,
        )


class SourceCandidateRanker:
    """Deterministic, task-aware quality ranking before collection safety/budget."""

    def score(
        self,
        result: WebSearchResult,
        research_task: ResearchTask,
        *,
        existing_sources: list[SourceDocument] | None = None,
    ) -> RankedSourceCandidate:
        sources = existing_sources or []
        confirmed = _confirmed_domains(research_task, sources)
        probable = _probable_domains(research_task)
        rejected = _rejected_domains(research_task)
        confidence = _official_confidence(
            result,
            research_task,
            confirmed,
            probable,
            rejected,
        )
        signals = _signals(result, research_task)
        role = _source_role(confidence, signals)
        dimension = canonical_dimension(research_task.dimension)
        relevance = _relevance_score(result, research_task, confidence, signals)
        dimension_fit = _dimension_fit_score(
            dimension,
            role,
            confidence,
            relevance,
            signals,
            result,
        )
        authority = _authority_score(role, signals["experience_objective"])
        freshness = _freshness_score(result, dimension)
        penalty_values = _penalties(
            relevance,
            confidence,
            signals,
            research_task,
            result,
        )
        penalty_score = sum(value for _name, value in penalty_values)
        raw_score = round(
            relevance + dimension_fit + authority + freshness + penalty_score,
            2,
        )
        gate_reason = _relevance_gate_reason(dimension, confidence, signals)
        if gate_reason and raw_score > RELEVANCE_GATE_SCORE_CAP:
            gated_score = min(
                RELEVANCE_GATE_SCORE_CAP,
                round(relevance * RELEVANCE_GATE_SCORE_MULTIPLIER, 2),
            )
            gate_adjustment = round(gated_score - raw_score, 2)
            penalty_values.append((f"relevance_gate_{gate_reason}", gate_adjustment))
            penalty_score = round(penalty_score + gate_adjustment, 2)
            final_score = gated_score
        else:
            final_score = raw_score
        return RankedSourceCandidate(
            result=result,
            domain=hostname(result.url),
            source_role=role,
            official_confidence=confidence,
            relevance_score=relevance,
            dimension_fit_score=dimension_fit,
            authority_score=authority,
            freshness_score=freshness,
            penalties=tuple(f"{name}:{value:g}" for name, value in penalty_values),
            penalty_score=penalty_score,
            final_score=final_score,
        )

    def rank(
        self,
        results: list[WebSearchResult],
        research_task: ResearchTask,
        *,
        existing_sources: list[SourceDocument] | None = None,
    ) -> list[RankedSourceCandidate]:
        remaining = [
            self.score(item, research_task, existing_sources=existing_sources)
            for item in results
        ]
        ranked: list[RankedSourceCandidate] = []
        domain_counts: Counter[str] = Counter()
        role_priority = {
            SourceRole.PRIMARY: 0,
            SourceRole.AUTHORITATIVE_SECONDARY: 1,
            SourceRole.GENERAL_THIRD_PARTY: 2,
            SourceRole.COMMUNITY: 3,
            SourceRole.LOW_QUALITY: 4,
        }
        while remaining:
            def selection_key(
                item: RankedSourceCandidate,
            ) -> tuple[int, float, float, int, str]:
                count = domain_counts[registrable_domain(item.domain)]
                step = 2.0 if item.source_role == SourceRole.PRIMARY else 4.0
                diversity_penalty = min(12.0, count * step)
                return (
                    -role_priority[item.source_role],
                    item.final_score - diversity_penalty,
                    item.final_score,
                    -item.result.rank,
                    item.result.url,
                )

            chosen = max(remaining, key=selection_key)
            domain_key = registrable_domain(chosen.domain)
            step = 2.0 if chosen.source_role == SourceRole.PRIMARY else 4.0
            diversity_penalty = min(12.0, domain_counts[domain_key] * step)
            ranked.append(
                replace(
                    chosen,
                    diversity_penalty=diversity_penalty,
                    quality_rank=len(ranked) + 1,
                )
            )
            domain_counts[domain_key] += 1
            remaining.remove(chosen)
        return ranked
