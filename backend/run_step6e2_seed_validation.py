from __future__ import annotations

import json
import re
from pathlib import Path

from app.tools.web_collector import WebCollectorTool


SNAPSHOT_DIR = Path(__file__).resolve().parent / "app" / "data" / "snapshots" / "online_education"


def normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


STOPWORDS = {
    "this", "that", "with", "from", "into", "using", "used", "includes",
    "supports", "provides", "their", "than", "rather", "because", "through",
    "system", "product", "classroom", "bigbluebutton", "classin",
}


def term_coverage(expected: str, page_text: str) -> float:
    lower_page = page_text.casefold()
    english = {
        token for token in re.findall(r"[a-z][a-z0-9_-]{3,}", expected.casefold())
        if token not in STOPWORDS
    }
    chinese_segments = re.findall(r"[\u3400-\u9fff]{4,}", expected)
    chinese_bigrams = {
        segment[index:index + 2]
        for segment in chinese_segments
        for index in range(len(segment) - 1)
    }
    terms = english | chinese_bigrams
    if len(terms) < 3:
        return 0.0
    return sum(term.casefold() in lower_page for term in terms) / len(terms)


def main() -> None:
    sources = json.loads((SNAPSHOT_DIR / "sources.json").read_text(encoding="utf-8"))
    evidence = json.loads((SNAPSHOT_DIR / "evidence.json").read_text(encoding="utf-8"))
    evidence_by_source: dict[str, list[dict]] = {}
    for item in evidence:
        evidence_by_source.setdefault(item["source_id"], []).append(item)

    tool = WebCollectorTool(timeout_seconds=20, max_bytes=3_000_000)
    fetched_count = 0
    matched = 0
    term_supported = 0
    checked = 0
    accessible_checked = 0
    for source in sources:
        source_evidence = evidence_by_source.get(source["id"], [])
        try:
            page = tool.fetch(source["url"])
            fetched_count += 1
            accessible_checked += len(source_evidence)
            page_text = normalize(page.text)
            source_matches = sum(
                normalize(item["snippet"]) in page_text
                for item in source_evidence
            )
            source_term_supported = sum(
                max(
                    term_coverage(item["snippet"], page.text),
                    term_coverage(item["normalized_fact"], page.text),
                ) >= 0.5
                for item in source_evidence
            )
            matched += source_matches
            term_supported += source_term_supported
            checked += len(source_evidence)
            print(
                f"source={source['id']} fetch=PASS chars={len(page.text)} "
                f"evidence_exact={source_matches}/{len(source_evidence)} "
                f"evidence_term_supported={source_term_supported}/{len(source_evidence)}"
            )
        except Exception as exc:
            checked += len(source_evidence)
            print(f"source={source['id']} fetch=FAIL error={type(exc).__name__}: {exc}")
    tool.close()
    rate = matched / checked if checked else 0.0
    print(f"fetched_sources={fetched_count}/{len(sources)}")
    print(f"manual_evidence_exact_match={matched}/{checked}")
    print(f"manual_evidence_exact_match_rate={rate:.3f}")
    print(f"manual_evidence_term_supported={term_supported}/{checked}")
    print(f"manual_evidence_term_supported_rate={(term_supported / checked if checked else 0.0):.3f}")
    print(f"accessible_evidence_term_supported={term_supported}/{accessible_checked}")
    print(f"accessible_evidence_term_supported_rate={(term_supported / accessible_checked if accessible_checked else 0.0):.3f}")


if __name__ == "__main__":
    main()
