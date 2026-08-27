from __future__ import annotations

from app.collection.service import _probable_official_host
from app.schemas import WebSearchResult


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def result(
    *,
    title: str,
    url: str,
    snippet: str,
    rank: int = 1,
) -> WebSearchResult:
    return WebSearchResult(
        task_id="task_official_host_detection_fix",
        research_task_id="research_official_host_detection_fix",
        search_attempt_id="attempt_official_host_detection_fix",
        provider="fake",
        query="official discovery",
        rank=rank,
        title=title,
        url=url,
        snippet=snippet,
    )


def main() -> None:
    cases = [
        (
            "快手",
            result(
                title="快手开放平台 - 官方开发者文档",
                url="https://open.kuaishou.com/platform/docs",
                snippet="快手官方开放平台提供开发者 API 文档。",
            ),
            "open.kuaishou.com",
        ),
        (
            "快手",
            result(
                title="快手科技投资者关系",
                url="https://ir.kuaishou.com/financial-information",
                snippet="快手官方 Investor Relations information.",
            ),
            "ir.kuaishou.com",
        ),
        (
            "抖音",
            result(
                title="抖音开放平台开发者文档",
                url="https://developer.open-douyin.com/docs/resource/zh-CN/",
                snippet="抖音开放平台官方 developer documentation。",
            ),
            "developer.open-douyin.com",
        ),
    ]
    for target, candidate, expected in cases:
        require(
            _probable_official_host(candidate, target=target) == expected,
            f"真实 official host 案例未识别：{expected}",
        )

    rejected = [
        result(
            title="快手官方开发者文档完整教程",
            url="https://blog.csdn.net/example/article/details/1",
            snippet="快手开放平台 API 教程。",
        ),
        result(
            title="快手开放平台官方文档怎么看？",
            url="https://www.zhihu.com/question/1",
            snippet="快手开发者经验讨论。",
        ),
        result(
            title="Kuaishou Official Developer Documentation Guide",
            url="https://medium.com/example/kuaishou-guide",
            snippet="Kuaishou developer platform guide.",
        ),
        result(
            title="快手产品功能分析",
            url="https://analysis.example.com/kuaishou",
            snippet="介绍快手产品功能，但没有一手来源信号。",
        ),
        result(
            title="Developer Documentation",
            url="https://developer.example.com/docs",
            snippet="通用开发者文档，没有出现当前目标。",
        ),
    ]
    targets = ["快手", "快手", "Kuaishou", "快手", "快手"]
    require(
        all(
            not _probable_official_host(candidate, target=target)
            for candidate, target in zip(rejected, targets)
        ),
        "社区/媒体/弱相关页面被误判为 probable official host",
    )

    print("check_official_host_detection_fix: PASS")
    print("kuaishou_open_probable=true")
    print("kuaishou_ir_probable=true")
    print("douyin_developer_probable=true")
    print("community_media_rejected=true")
    print("confidence=probable_only")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
