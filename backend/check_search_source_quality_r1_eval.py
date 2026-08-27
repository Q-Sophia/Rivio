from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.collection.source_quality import (
    MIN_COLLECTION_SCORE,
    RANKING_VERSION,
    RankedSourceCandidate,
    SourceCandidateRanker,
)
from app.schemas import ResearchPlan, ResearchTask, SourceDocument, WebSearchResult
from app.tools.web_collector import URLSafetyPolicy


TASK_ID = "task_user_829e249408ef"
TOP_K = 5
ROLE_ORDER = [
    "PRIMARY",
    "AUTHORITATIVE_SECONDARY",
    "COMMUNITY",
    "LOW_QUALITY",
]
BASE_DIR = Path(__file__).resolve().parent
RUN_DIR = BASE_DIR / "app" / "data" / "runs" / TASK_ID
EVAL_DIR = BASE_DIR / "app" / "data" / "evaluations" / "search_source_quality_r1"
GOLDEN_CSV = EVAL_DIR / "trae_source_candidates_labeled_v1.csv"
JSON_OUTPUT = EVAL_DIR / "source_quality_eval_v1.json"
MARKDOWN_OUTPUT = EVAL_DIR / "source_quality_eval_v1.md"


def _read_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _rounded(value: float) -> float:
    return round(value, 2)


def _metric_block(actual: list[bool], predicted: list[bool]) -> dict[str, Any]:
    tp = sum(a and p for a, p in zip(actual, predicted))
    fp = sum(not a and p for a, p in zip(actual, predicted))
    tn = sum(not a and not p for a, p in zip(actual, predicted))
    fn = sum(a and not p for a, p in zip(actual, predicted))
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall),
        "false_positive_rate": _ratio(fp, fp + tn),
        "false_negative_rate": _ratio(fn, fn + tp),
    }


def _golden_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row["research_task_id"].strip(),
        row["query"].strip(),
        row["title"].strip(),
        row["url"].strip(),
        row["snippet"].strip(),
    )


def _result_key(result: WebSearchResult) -> tuple[str, str, str, str, str]:
    return (
        result.research_task_id.strip(),
        result.query.strip(),
        result.title.strip(),
        result.url.strip(),
        result.snippet.strip(),
    )


def _sample(candidate: RankedSourceCandidate, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_task_id": row["research_task_id"],
        "dimension": row["dimension"],
        "title": row["title"],
        "url": row["url"],
        "predicted_score": candidate.final_score,
        "predicted_role": candidate.source_role.value,
        "human_relevance": int(row["human_relevance"]),
        "human_role": row["human_source_role"],
        "human_should_collect": row["human_should_collect"].lower(),
        "human_reason": row["human_reason"],
    }


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = [str(item).replace("\n", " ").replace("|", "\\|") for item in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _build_markdown(report: dict[str, Any]) -> str:
    role = report["role_evaluation"]
    collect = report["should_collect_evaluation"]
    relevance = report["score_relevance_evaluation"]
    topk = report["top_k_evaluation"]
    lines = [
        "# Search Source Quality R1 Offline Eval",
        "",
        f"- Task: `{report['metadata']['task_id']}`",
        f"- Golden samples: {report['metadata']['golden_sample_count']}",
        f"- Ranking version: `{report['metadata']['ranking_version']}`",
        f"- Minimum quality line: {report['metadata']['minimum_collection_score']}",
        "- External API / model calls: 0",
        "",
        "## A. Predicted source role",
        "",
        f"Overall accuracy: **{role['overall_accuracy']:.2%}** ({role['correct']}/{role['total']})",
        "",
    ]
    lines += _markdown_table(
        ["Human role", "Samples", "Precision", "Recall"],
        [
            [
                name,
                role["per_role"][name]["sample_count"],
                f"{role['per_role'][name]['precision']:.2%}",
                f"{role['per_role'][name]['recall']:.2%}",
            ]
            for name in ROLE_ORDER
        ],
    )
    lines += ["", "Confusion matrix（row=human, column=predicted）", ""]
    lines += _markdown_table(
        ["Human \\ Predicted", *ROLE_ORDER],
        [
            [human, *[role["confusion_matrix"][human][pred] for pred in ROLE_ORDER]]
            for human in ROLE_ORDER
        ],
    )
    lines += [
        "",
        f"- COMMUNITY → LOW_QUALITY: {role['focus']['community_as_low_quality']['count']}",
        "- LOW_QUALITY → AUTHORITATIVE_SECONDARY / COMMUNITY: "
        f"{role['focus']['low_quality_as_secondary_or_community']['count']}",
        "- PRIMARY stability: "
        f"precision {role['focus']['primary_stability']['precision']:.2%}, "
        f"recall {role['focus']['primary_stability']['recall']:.2%}",
        "",
        f"### All role misclassifications ({len(role['misclassified'])})",
        "",
    ]
    lines += _markdown_table(
        ["Title", "URL", "Predicted", "Human", "Human reason"],
        [
            [item["title"], item["url"], item["predicted_role"], item["human_role"], item["human_reason"]]
            for item in role["misclassified"]
        ],
    )

    main = collect["production_selection_url_level"]
    eligible = collect["minimum_score_eligibility_diagnostic"]
    lines += [
        "",
        "## B. Should collect",
        "",
        "主指标按完整 117 条候选执行当前 Ranker → URL safety → minimum score → "
        "每 ResearchTask 5 条预算；同一 ResearchTask 的重复 URL 按实际采集 URL 合并。",
        "",
    ]
    lines += _markdown_table(
        ["Decision", "TP", "FP", "TN", "FN", "Precision", "Recall", "F1", "FPR", "FNR"],
        [
            [
                "Production selection",
                main["tp"], main["fp"], main["tn"], main["fn"],
                f"{main['precision']:.2%}", f"{main['recall']:.2%}", f"{main['f1']:.2%}",
                f"{main['false_positive_rate']:.2%}", f"{main['false_negative_rate']:.2%}",
            ],
            [
                "Score >= minimum (diagnostic)",
                eligible["tp"], eligible["fp"], eligible["tn"], eligible["fn"],
                f"{eligible['precision']:.2%}", f"{eligible['recall']:.2%}", f"{eligible['f1']:.2%}",
                f"{eligible['false_positive_rate']:.2%}", f"{eligible['false_negative_rate']:.2%}",
            ],
        ],
    )
    for label, key in (("False positives", "false_positives"), ("False negatives", "false_negatives")):
        samples = collect[key]
        lines += ["", f"### {label} ({len(samples)})", ""]
        lines += _markdown_table(
            ["ResearchTask", "Dimension", "Title", "URL", "Score", "Predicted role", "Human relevance", "Human role", "Human reason"],
            [
                [
                    item["research_task_id"], item["dimension"], item["title"], item["url"],
                    item["predicted_score"], item["predicted_role"], item["human_relevance"],
                    item["human_role"], item["human_reason"],
                ]
                for item in samples
            ],
        )

    lines += ["", "## C. Score vs human relevance", ""]
    lines += _markdown_table(
        ["Human relevance", "Count", "Mean", "Median", "Min", "Max"],
        [
            [level, *[relevance["by_human_relevance"][str(level)][name] for name in ("count", "mean", "median", "min", "max")]]
            for level in range(4)
        ],
    )
    pairwise = relevance["within_research_task_pairwise"]
    lines += [
        "",
        f"Within-ResearchTask pairwise ranking accuracy: **{pairwise['accuracy']:.2%}** "
        f"({pairwise['correct_pairs']}/{pairwise['comparable_pairs']}, ties={pairwise['tie_pairs']}).",
        "",
        relevance["relevance_3_vs_0_1"]["interpretation"],
        "",
        "## D. Top-5",
        "",
        f"ResearchTasks: {topk['research_task_count']}; fully labeled production Top5: "
        f"{topk['fully_covered_task_count']}.",
        "",
        topk["formal_precision_at_5_statement"],
        "",
    ]
    lines += _markdown_table(
        ["ResearchTask", "Selected", "Labeled", "Fully covered", "Formal P@5", "Labeled-only proxy"],
        [
            [
                item["research_task_id"], item["production_top_k_size"], item["labeled_top_k_count"],
                item["fully_covered"],
                item["formal_precision_at_5"] if item["formal_precision_at_5"] is not None else "N/A",
                item["labeled_only_proxy_precision"] if item["labeled_only_proxy_precision"] is not None else "N/A",
            ]
            for item in topk["per_research_task"]
        ],
    )

    lines += ["", "## E. Known regressions", ""]
    for group, items in report["known_regressions"].items():
        if group == "authority_over_relevance_assessment":
            continue
        lines += [f"### {group}", ""]
        lines += _markdown_table(
            ["Title", "URL", "Score", "Role", "Eligible", "Selected", "Human relevance", "Human collect"],
            [
                [
                    item["title"], item["url"], item["predicted_score"], item["predicted_role"],
                    item["eligible"], item["production_selected"], item["human_relevance"], item["human_should_collect"],
                ]
                for item in items
            ],
        )
        lines.append("")
    lines += [
        "Authority-over-relevance assessment: "
        + report["known_regressions"]["authority_over_relevance_assessment"],
        "",
        "## Final conclusion",
        "",
        f"A. Acceptable baseline: **{report['conclusion']['acceptable_baseline']}** — "
        f"{report['conclusion']['baseline_reason']}",
        "",
        "B. Biggest errors: " + "; ".join(report["conclusion"]["biggest_errors"]),
        "",
        f"C. Immediate production Ranker change recommended: **{report['conclusion']['immediate_ranker_change_recommended']}**.",
        "",
        "Minimal recommendations（not implemented）:",
        "",
    ]
    lines += [f"{index}. {item}" for index, item in enumerate(report["conclusion"]["recommendations"], start=1)]
    return "\n".join(lines) + "\n"


def main() -> None:
    golden_hash_before = hashlib.sha256(GOLDEN_CSV.read_bytes()).hexdigest()
    with GOLDEN_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        golden = list(csv.DictReader(handle))
    if not golden:
        raise AssertionError("Golden CSV is empty")
    required_human_fields = {
        "human_source_role",
        "human_relevance",
        "human_should_collect",
        "human_reason",
    }
    for index, row in enumerate(golden, start=2):
        if not all(row.get(field, "").strip() for field in required_human_fields):
            raise AssertionError(f"Golden CSV row {index} has incomplete human labels")

    research_tasks = [ResearchTask.model_validate(item) for item in _read_json(RUN_DIR / "research_tasks.json")]
    task_by_id = {item.id: item for item in research_tasks}
    search_results = [WebSearchResult.model_validate(item) for item in _read_json(RUN_DIR / "web_search_results.json")]
    source_documents = [SourceDocument.model_validate(item) for item in _read_json(RUN_DIR / "sources.json")]
    plans = [ResearchPlan.model_validate(item) for item in _read_json(RUN_DIR / "research_plans.json")]
    source_budget = plans[-1].budget.max_sources_per_task if plans else TOP_K
    if source_budget != TOP_K:
        raise AssertionError(f"Expected production max_sources_per_task={TOP_K}, got {source_budget}")

    result_by_key: dict[tuple[str, str, str, str, str], list[WebSearchResult]] = defaultdict(list)
    for result in search_results:
        result_by_key[_result_key(result)].append(result)
    mapped_results: list[WebSearchResult] = []
    for row in golden:
        matches = result_by_key[_golden_key(row)]
        if len(matches) != 1:
            raise AssertionError(f"Golden row maps to {len(matches)} WebSearchResults: {row['url']}")
        mapped_results.append(matches[0])

    ranker = SourceCandidateRanker()
    safety = URLSafetyPolicy(resolve_dns=False)
    all_candidates: dict[str, RankedSourceCandidate] = {}
    ranked_by_task: dict[str, list[RankedSourceCandidate]] = {}
    selected_result_ids: set[str] = set()
    selected_task_urls: set[tuple[str, str]] = set()
    topk_rows: list[dict[str, Any]] = []
    golden_result_ids = {item.id for item in mapped_results}

    for research_task in research_tasks:
        task_results = [item for item in search_results if item.research_task_id == research_task.id]
        ranked = ranker.rank(task_results, research_task, existing_sources=source_documents)
        ranked_by_task[research_task.id] = ranked
        all_candidates.update({item.result.id: item for item in ranked})
        selected: list[RankedSourceCandidate] = []
        seen_urls: set[str] = set()
        for candidate in ranked:
            if candidate.final_score < MIN_COLLECTION_SCORE or candidate.result.url in seen_urls:
                continue
            try:
                safety.validate(candidate.result.url)
            except Exception:
                continue
            selected.append(candidate)
            seen_urls.add(candidate.result.url)
            selected_result_ids.add(candidate.result.id)
            selected_task_urls.add((research_task.id, candidate.result.url))
            if len(selected) >= source_budget:
                break

        selected_labels = [
            golden[mapped_results.index(item.result)]
            for item in selected
            if item.result.id in golden_result_ids
        ]
        full_coverage = len(selected) == TOP_K and len(selected_labels) == TOP_K
        labeled_yes = sum(row["human_should_collect"].lower() == "yes" for row in selected_labels)
        topk_rows.append(
            {
                "research_task_id": research_task.id,
                "dimension": research_task.dimension,
                "production_top_k_size": len(selected),
                "selected_result_ids": [item.result.id for item in selected],
                "selected_urls": [item.result.url for item in selected],
                "labeled_top_k_count": len(selected_labels),
                "fully_covered": full_coverage,
                "formal_precision_at_5": _ratio(labeled_yes, TOP_K) if full_coverage else None,
                "formal_false_positive_at_5": TOP_K - labeled_yes if full_coverage else None,
                "formal_low_quality_at_5": (
                    sum(row["human_source_role"] == "LOW_QUALITY" for row in selected_labels)
                    if full_coverage
                    else None
                ),
                "labeled_only_proxy_precision": (
                    _ratio(labeled_yes, len(selected_labels)) if selected_labels else None
                ),
            }
        )

    evaluated: list[dict[str, Any]] = []
    prediction_drift: list[dict[str, Any]] = []
    for row, result in zip(golden, mapped_results):
        candidate = all_candidates[result.id]
        item = dict(row)
        item["human_relevance"] = int(row["human_relevance"])
        item["predicted_role_recomputed"] = candidate.source_role.value
        item["predicted_score_recomputed"] = candidate.final_score
        item["eligible"] = candidate.final_score >= MIN_COLLECTION_SCORE
        item["production_selected"] = (result.research_task_id, result.url) in selected_task_urls
        evaluated.append(item)
        if (
            row["predicted_source_role"] != candidate.source_role.value
            or abs(float(row["predicted_score"]) - candidate.final_score) > 0.01
        ):
            prediction_drift.append(
                {
                    "url": result.url,
                    "csv_role": row["predicted_source_role"],
                    "recomputed_role": candidate.source_role.value,
                    "csv_score": float(row["predicted_score"]),
                    "recomputed_score": candidate.final_score,
                }
            )

    confusion = {human: {pred: 0 for pred in ROLE_ORDER} for human in ROLE_ORDER}
    for item in evaluated:
        confusion[item["human_source_role"]][item["predicted_role_recomputed"]] += 1
    role_correct = sum(
        item["human_source_role"] == item["predicted_role_recomputed"] for item in evaluated
    )
    per_role: dict[str, dict[str, Any]] = {}
    for name in ROLE_ORDER:
        actual_count = sum(item["human_source_role"] == name for item in evaluated)
        predicted_count = sum(item["predicted_role_recomputed"] == name for item in evaluated)
        true_positive = confusion[name][name]
        per_role[name] = {
            "sample_count": actual_count,
            "predicted_count": predicted_count,
            "true_positive": true_positive,
            "precision": _ratio(true_positive, predicted_count),
            "recall": _ratio(true_positive, actual_count),
        }
    misclassified = [
        {
            "title": item["title"],
            "url": item["url"],
            "predicted_role": item["predicted_role_recomputed"],
            "human_role": item["human_source_role"],
            "human_reason": item["human_reason"],
        }
        for item in evaluated
        if item["human_source_role"] != item["predicted_role_recomputed"]
    ]

    actual_collect = [item["human_should_collect"].lower() == "yes" for item in evaluated]
    predicted_collect = [bool(item["production_selected"]) for item in evaluated]
    predicted_eligible = [bool(item["eligible"]) for item in evaluated]
    collect_metrics = _metric_block(actual_collect, predicted_collect)
    eligible_metrics = _metric_block(actual_collect, predicted_eligible)
    false_positives = [
        _sample(all_candidates[result.id], item)
        for item, result, actual, predicted in zip(evaluated, mapped_results, actual_collect, predicted_collect)
        if predicted and not actual
    ]
    false_negatives = [
        _sample(all_candidates[result.id], item)
        for item, result, actual, predicted in zip(evaluated, mapped_results, actual_collect, predicted_collect)
        if actual and not predicted
    ]

    relevance_groups: dict[str, dict[str, Any]] = {}
    for level in range(4):
        scores = [item["predicted_score_recomputed"] for item in evaluated if item["human_relevance"] == level]
        relevance_groups[str(level)] = {
            "count": len(scores),
            "mean": _rounded(statistics.mean(scores)),
            "median": _rounded(statistics.median(scores)),
            "min": min(scores),
            "max": max(scores),
        }
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evaluated:
        by_task[item["research_task_id"]].append(item)
    comparable_pairs = correct_pairs = tie_pairs = 0
    for items in by_task.values():
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                if left["human_relevance"] == right["human_relevance"]:
                    continue
                comparable_pairs += 1
                high, low = (left, right) if left["human_relevance"] > right["human_relevance"] else (right, left)
                if high["predicted_score_recomputed"] > low["predicted_score_recomputed"]:
                    correct_pairs += 1
                elif high["predicted_score_recomputed"] == low["predicted_score_recomputed"]:
                    tie_pairs += 1
    high_scores = [item["predicted_score_recomputed"] for item in evaluated if item["human_relevance"] == 3]
    low_scores = [item["predicted_score_recomputed"] for item in evaluated if item["human_relevance"] in {0, 1}]
    high_low_gap = statistics.mean(high_scores) - statistics.mean(low_scores)
    high_beating_low = _ratio(
        sum(high > low for high in high_scores for low in low_scores),
        len(high_scores) * len(low_scores),
    )
    interpretation = (
        f"relevance=3 mean is {_rounded(high_low_gap)} points above relevance=0/1; "
        f"a random relevance=3 sample outscores a random relevance=0/1 sample {high_beating_low:.2%} of the time. "
        "This is descriptive only because the Golden Set is small and stratified rather than random."
    )

    def known_items(predicate: Any) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for item, result in zip(evaluated, mapped_results):
            if predicate(item):
                sample = _sample(all_candidates[result.id], item)
                sample["eligible"] = item["eligible"]
                sample["production_selected"] = item["production_selected"]
                values.append(sample)
        return values

    known = {
        "should_demote_360docs": known_items(lambda item: "360docs" in item["url"]),
        "should_demote_generic_csdn_positioning": known_items(
            lambda item: "csdn.net" in item["url"] and item["dimension"] == "产品定位"
        ),
        "should_demote_m_php_cn_seo": known_items(lambda item: "m.php.cn" in item["url"]),
        "should_keep_official_docs": known_items(
            lambda item: "docs.trae." in item["url"]
        ),
        "should_keep_official_permissions": known_items(
            lambda item: "权限" in item["title"] and "docs.trae." in item["url"]
        ),
        "should_keep_trae_3": known_items(lambda item: "trae 3.0" in item["title"].casefold()),
        "should_keep_solo_agent": known_items(lambda item: "SOLO Agent" in item["title"]),
        "official_but_low_relevance_terms": known_items(
            lambda item: "terms-of-service" in item["url"] or "terms of service" in item["title"].casefold()
        ),
        "official_but_low_relevance_trae_cn_tutorials": known_items(
            lambda item: "trae.cn" in item["url"] and "docs.trae.cn" not in item["url"]
        ),
    }
    official_low = known["official_but_low_relevance_terms"] + known["official_but_low_relevance_trae_cn_tutorials"]
    official_low_selected = sum(item["production_selected"] for item in official_low)
    official_low_eligible = sum(item["eligible"] for item in official_low)
    known["authority_over_relevance_assessment"] = (
        f"Among {len(official_low)} labeled official-domain but low-relevance Terms/tutorial candidates, "
        f"{official_low_eligible} clear the minimum score and {official_low_selected} enter production selection. "
        + (
            "This confirms authority can overpower task relevance in the current policy."
            if official_low_selected
            else "The current budget selection prevents them from displacing higher-ranked sources, although threshold eligibility may still be too permissive."
        )
    )

    fully_covered = [item for item in topk_rows if item["fully_covered"]]
    if fully_covered:
        topk_statement = (
            f"Formal Precision@5 is available for {len(fully_covered)} ResearchTasks; "
            "see per-task metrics."
        )
    else:
        topk_statement = (
            "真实 Precision@5 暂不可完整计算：没有任何 ResearchTask 的 production Top5 被 Golden Set 完整覆盖。"
            "下表 labeled-only proxy 只描述 Top5 中已标注的子集，不能冒充正式 Precision@5。"
        )

    role_accuracy = _ratio(role_correct, len(evaluated))
    # Eval judgement only; this does not alter or tune the production ranker.
    acceptable = (
        role_accuracy >= 0.75
        and collect_metrics["precision"] >= 0.70
        and collect_metrics["recall"] >= 0.70
        and _ratio(correct_pairs, comparable_pairs) >= 0.70
    )
    biggest_errors: list[str] = []
    low_as_good = sum(
        item["human_source_role"] == "LOW_QUALITY"
        and item["predicted_role_recomputed"] in {"AUTHORITATIVE_SECONDARY", "COMMUNITY"}
        for item in evaluated
    )
    if low_as_good:
        biggest_errors.append(f"source role classification: {low_as_good} LOW_QUALITY samples are promoted to secondary/community")
    if collect_metrics["fp"]:
        biggest_errors.append(f"production selection false positives: {collect_metrics['fp']}")
    if official_low_selected or official_low_eligible:
        biggest_errors.append(
            f"authority/relevance balance: {official_low_eligible} low-relevance official candidates pass the score line"
        )
    if not biggest_errors:
        biggest_errors.append("no dominant error class in this small Golden Set")

    report: dict[str, Any] = {
        "metadata": {
            "task_id": TASK_ID,
            "golden_csv": str(GOLDEN_CSV),
            "golden_sha256": golden_hash_before,
            "golden_sample_count": len(golden),
            "full_search_result_count": len(search_results),
            "research_task_count": len(research_tasks),
            "ranking_version": RANKING_VERSION,
            "minimum_collection_score": MIN_COLLECTION_SCORE,
            "max_sources_per_task": source_budget,
            "model_calls": 0,
            "external_network_calls": 0,
        },
        "input_validation": {
            "golden_rows_matched_to_search_results": len(mapped_results),
            "prediction_drift_count": len(prediction_drift),
            "prediction_drift": prediction_drift,
        },
        "role_evaluation": {
            "correct": role_correct,
            "total": len(evaluated),
            "overall_accuracy": role_accuracy,
            "per_role": per_role,
            "confusion_matrix": confusion,
            "misclassified": misclassified,
            "focus": {
                "community_as_low_quality": {
                    "count": confusion["COMMUNITY"]["LOW_QUALITY"],
                    "samples": [
                        item for item in misclassified
                        if item["human_role"] == "COMMUNITY" and item["predicted_role"] == "LOW_QUALITY"
                    ],
                },
                "low_quality_as_secondary_or_community": {
                    "count": confusion["LOW_QUALITY"]["AUTHORITATIVE_SECONDARY"] + confusion["LOW_QUALITY"]["COMMUNITY"],
                    "samples": [
                        item for item in misclassified
                        if item["human_role"] == "LOW_QUALITY"
                        and item["predicted_role"] in {"AUTHORITATIVE_SECONDARY", "COMMUNITY"}
                    ],
                },
                "primary_stability": {
                    "actual_primary": per_role["PRIMARY"]["sample_count"],
                    "predicted_primary": per_role["PRIMARY"]["predicted_count"],
                    "correct_primary": per_role["PRIMARY"]["true_positive"],
                    "precision": per_role["PRIMARY"]["precision"],
                    "recall": per_role["PRIMARY"]["recall"],
                },
            },
        },
        "should_collect_evaluation": {
            "decision_basis": (
                "Recomputed full-pool production policy: quality rank, offline URL safety, "
                "minimum score, duplicate URL suppression, max_sources_per_task; Golden duplicates "
                "are evaluated at ResearchTask+URL level because Collector fetches the URL once."
            ),
            "production_selection_url_level": collect_metrics,
            "minimum_score_eligibility_diagnostic": eligible_metrics,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
        },
        "score_relevance_evaluation": {
            "by_human_relevance": relevance_groups,
            "within_research_task_pairwise": {
                "comparable_pairs": comparable_pairs,
                "correct_pairs": correct_pairs,
                "tie_pairs": tie_pairs,
                "accuracy": _ratio(correct_pairs, comparable_pairs),
            },
            "relevance_3_vs_0_1": {
                "relevance_3_mean": _rounded(statistics.mean(high_scores)),
                "relevance_0_1_mean": _rounded(statistics.mean(low_scores)),
                "mean_gap": _rounded(high_low_gap),
                "probability_relevance_3_outscores_0_1": high_beating_low,
                "interpretation": interpretation,
            },
        },
        "top_k_evaluation": {
            "top_k": TOP_K,
            "research_task_count": len(topk_rows),
            "fully_covered_task_count": len(fully_covered),
            "formal_precision_at_5_statement": topk_statement,
            "per_research_task": topk_rows,
        },
        "known_regressions": known,
        "conclusion": {
            "acceptable_baseline": acceptable,
            "baseline_reason": (
                "role accuracy, production collect precision/recall, and pairwise ranking all meet the pre-declared 0.70/0.75 diagnostic floor."
                if acceptable
                else "at least one of role accuracy, production collect precision/recall, or pairwise ranking remains below the diagnostic floor."
            ),
            "biggest_errors": biggest_errors[:3],
            "immediate_ranker_change_recommended": not acceptable,
            "recommendations": [
                "Separate source-role identity from task relevance more explicitly, especially for official-domain generic tutorials and Terms pages.",
                "Add deterministic page-intent signals for SEO/FAQ/tutorial surfaces so high target mention alone cannot promote weak evidence candidates.",
                "Expand labels to cover every production Top5 before treating Precision@5 as a release gate.",
            ],
        },
    }

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_OUTPUT.write_text(_build_markdown(report), encoding="utf-8")
    golden_hash_after = hashlib.sha256(GOLDEN_CSV.read_bytes()).hexdigest()
    if golden_hash_after != golden_hash_before:
        raise AssertionError("Golden CSV changed during evaluation")

    print("check_search_source_quality_r1_eval: PASS")
    print(f"golden_samples={len(golden)}")
    print(f"role_accuracy={role_accuracy:.4f}")
    print(
        "should_collect="
        f"tp:{collect_metrics['tp']},fp:{collect_metrics['fp']},tn:{collect_metrics['tn']},fn:{collect_metrics['fn']},"
        f"precision:{collect_metrics['precision']:.4f},recall:{collect_metrics['recall']:.4f},f1:{collect_metrics['f1']:.4f}"
    )
    print(f"pairwise_ranking_accuracy={_ratio(correct_pairs, comparable_pairs):.4f}")
    print(f"fully_labeled_top5_tasks={len(fully_covered)}")
    print(f"acceptable_baseline={acceptable}")
    print(f"json_output={JSON_OUTPUT}")
    print(f"markdown_output={MARKDOWN_OUTPUT}")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
