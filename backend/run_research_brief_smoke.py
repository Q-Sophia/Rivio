from app.intake.service import IntentDraftService


CASES = [
    "请调研 ClassIn 的竞品分析",
    "比较 ClassIn、BigBlueButton 和腾讯云实时互动",
    "我打算开发一个在线教育产品，帮我调研一下",
    "帮我分析一下 ClassIn 的产品情况",
]


def main():
    service = IntentDraftService()

    for index, text in enumerate(CASES, start=1):
        print()
        print("=" * 80)
        print(f"CASE {index}: {text}")

        try:
            draft, meta = service.parse_request(text)
        except Exception as exc:
            print("ERROR:", type(exc).__name__, str(exc))
            continue

        print("research_mode =", draft.research_mode)
        print("primary_target =", draft.primary_target)
        print("comparison_targets =", draft.comparison_targets)
        print("reference_products =", draft.reference_products)

        print("target_profiling =", draft.target_profiling)
        print("market_scoping =", draft.market_scoping)
        print("competitor_discovery =", draft.competitor_discovery)
        print(
            "cross_competitor_comparison =",
            draft.cross_competitor_comparison,
        )
        print(
            "decision_oriented_analysis =",
            draft.decision_oriented_analysis,
        )
        print(
            "research_gap_tracking =",
            draft.research_gap_tracking,
        )

        print("industry =", draft.industry)
        print("competitors(legacy) =", draft.competitors)
        print("focus_areas =", draft.focus_areas)
        print("constraints =", draft.constraints)

        print("ready_for_confirmation =", draft.ready_for_confirmation)
        print("missing_fields =", draft.missing_fields)

        print("provider =", meta.get("provider"))
        print("model =", meta.get("model"))


if __name__ == "__main__":
    main()