from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# 当前已经稳定进入生产 Research 主链的搜索 Tool。
WEB_SEARCH_TOOL = "web_search"
ZHIHU_SEARCH_TOOL = "zhihu_search"


def _field(value: Any, name: str) -> str:
    """
    安全读取字典或对象中的字段，并统一字符串格式。

    例如：
        "User-Feedback"
        " user_feedback "
        "USER_FEEDBACK"

    都会尽量标准化成：
        "user_feedback"
    """

    if isinstance(value, Mapping):
        raw = value.get(name, "")
    else:
        raw = getattr(value, name, "")

    return "_".join(
        str(raw or "")
        .strip()
        .casefold()
        .replace("-", "_")
        .split()
    )


def resolve_tools(research_need: Any = None) -> list[str]:
    """
    TOOL-R1 兼容入口。

    当前生产 Research 主链只注册 web_search。

    这里不再根据 research_intent 决定：
        用户评价 -> 知乎
        定价 -> Web
        功能 -> Web

    也不负责：
        - 创建 Provider
        - 读取 API Key
        - 判断 HTTP / MCP

    后续 zhihu_search 通过独立 MCP Tool 接入后，
    多来源采集会由更上层的 Search orchestration 负责，
    而不是重新把来源判断写回 Router。
    """

    _ = research_need
    return [WEB_SEARCH_TOOL]


def research_intent_for_dimension(dimension: str) -> str:
    """
    根据研究维度推断 research_intent。

    research_intent 描述的是“研究什么”，
    不再决定“只能去哪里搜索”。
    """

    normalized = _field(
        {"dimension": dimension},
        "dimension",
    )
    compact = normalized.replace("_", "")

    if any(
        marker in compact
        for marker in (
            "用户评价",
            "用户反馈",
            "用户体验",
            "社区观点",
            "社区评价",
            "口碑",
            "userfeedback",
            "communityopinion",
            "userexperience",
        )
    ):
        return "user_feedback"

    if (
        "pricing" in normalized
        or "定价" in compact
        or "价格" in compact
    ):
        return "pricing"

    if (
        "feature" in normalized
        or "功能" in compact
        or "产品能力" in compact
    ):
        return "feature"

    return "product_information"