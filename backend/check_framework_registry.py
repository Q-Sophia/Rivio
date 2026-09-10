from __future__ import annotations

from app.frameworks import get_dimension, load_framework


EXPECTED_DIMENSIONS = [
    "market_positioning",
    "product_capability",
    "competitive_differentiation",
    "commercial_strategy",
    "customer_experience",
    "ecosystem",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    framework = load_framework("competitive_intelligence", "1.0.0")
    require(
        framework.default_dimension_ids == EXPECTED_DIMENSIONS,
        "Framework v1 维度或顺序与设计基线不一致",
    )
    require(
        [item.dimension_id for item in framework.dimensions]
        == EXPECTED_DIMENSIONS,
        "Framework definition 未完整加载六个维度",
    )
    require(
        len(framework.content_hash) == 64,
        "Framework 未生成稳定的 SHA-256 content hash",
    )
    for dimension in framework.dimensions:
        for field_name in (
            "research_intent",
            "research_questions",
            "required_facts",
            "query_templates",
            "preferred_source_types",
            "completion_criteria",
        ):
            require(bool(getattr(dimension, field_name)), f"{dimension.dimension_id} 缺少 {field_name}")

    customer = get_dimension("competitive_intelligence", "customer_experience")
    require(customer.evidence_dimension == "customer", "Registry dimension 映射错误")
    framework.dimensions.clear()
    require(
        len(load_framework("competitive_intelligence", "1.0.0").dimensions) == 6,
        "Registry 缓存对象被调用方意外修改",
    )
    try:
        load_framework("competitive_intelligence", "9.9.9")
    except KeyError:
        pass
    else:
        raise AssertionError("未注册版本没有被 Registry 拒绝")

    print("check_framework_registry: PASS")
    print("framework=competitive_intelligence@1.0.0")
    print("dimension_count=6")
    print("content_hash_verified=true")


if __name__ == "__main__":
    main()
