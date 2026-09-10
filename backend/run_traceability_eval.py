from __future__ import annotations

import argparse
import json
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_TASKS = [
    "task_user_46c24a3ccd54",
    "task_user_4143cb765d5e",
    "task_user_8666280bf195",
]

INVALID_CITATION_STATUSES = {
    "unsupported",
    "missing_evidence",
    "invalid_evidence",
}


def load_items(task_dir: Path, name: str) -> tuple[list[dict[str, Any]], str | None]:
    path = task_dir / f"{name}.json"
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{path.name}: {type(exc).__name__}: {exc}"
    if raw is None:
        return [], None
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)], None
    if isinstance(raw, dict):
        for key in ("items", "records", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)], None
        return [raw], None
    return [], f"{path.name}: unsupported JSON root {type(raw).__name__}"


def id_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get("id") or "").strip()
        if value:
            result[value] = item
    return result


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return []


def norm_text(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"\s+", " ", text).strip()


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def get_meta(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    return value if isinstance(value, dict) else {}


def evidence_chunk_id(evidence: dict[str, Any]) -> str:
    meta = get_meta(evidence)
    return str(first_nonempty(
        evidence.get("chunk_id"),
        evidence.get("source_chunk_id"),
        meta.get("chunk_id"),
        meta.get("source_chunk_id"),
    ) or "").strip()


def evidence_quote(evidence: dict[str, Any]) -> str:
    return norm_text(first_nonempty(
        evidence.get("exact_quote"),
        evidence.get("snippet"),
        evidence.get("quote"),
        evidence.get("evidence_quote"),
        evidence.get("content_excerpt"),
    ))


def source_url(source: dict[str, Any]) -> str:
    return str(first_nonempty(
        source.get("url"),
        source.get("final_url"),
        source.get("requested_url"),
    ) or "").strip()


def evidence_offsets(evidence: dict[str, Any]) -> tuple[int | None, int | None]:
    start = first_nonempty(
        evidence.get("source_text_start"),
        evidence.get("absolute_start"),
    )
    end = first_nonempty(
        evidence.get("source_text_end"),
        evidence.get("absolute_end"),
    )
    try:
        start = int(start) if start is not None else None
    except Exception:
        start = None
    try:
        end = int(end) if end is not None else None
    except Exception:
        end = None
    return start, end


def chunk_offsets(chunk: dict[str, Any]) -> tuple[int | None, int | None]:
    try:
        start = int(chunk.get("source_text_start"))
    except Exception:
        start = None
    try:
        end = int(chunk.get("source_text_end"))
    except Exception:
        end = None
    return start, end


def resolve_evidence_chunk(
    evidence: dict[str, Any],
    chunks: dict[str, dict[str, Any]],
) -> tuple[str | None, str]:
    direct = evidence_chunk_id(evidence)
    if direct:
        return (direct if direct in chunks else None), "direct"

    source_id = str(evidence.get("source_id") or "").strip()
    ev_start, ev_end = evidence_offsets(evidence)
    candidates: list[str] = []
    for cid, chunk in chunks.items():
        if source_id and str(chunk.get("source_id") or "").strip() != source_id:
            continue
        ch_start, ch_end = chunk_offsets(chunk)
        if (
            ev_start is not None
            and ev_end is not None
            and ch_start is not None
            and ch_end is not None
            and ch_start <= ev_start
            and ev_end <= ch_end
        ):
            candidates.append(cid)

    if len(candidates) == 1:
        return candidates[0], "inferred_by_offsets"

    quote = evidence_quote(evidence)
    if quote:
        quote_candidates = []
        for cid, chunk in chunks.items():
            if source_id and str(chunk.get("source_id") or "").strip() != source_id:
                continue
            if quote in norm_text(chunk.get("text")):
                quote_candidates.append(cid)
        if len(quote_candidates) == 1:
            return quote_candidates[0], "inferred_by_quote"

    return None, "unavailable"


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def evaluate_task(task_dir: Path) -> dict[str, Any]:
    errors: list[str] = []

    def read(name: str) -> list[dict[str, Any]]:
        items, err = load_items(task_dir, name)
        if err:
            errors.append(err)
        return items

    sources_l = read("sources")
    chunks_l = read("source_chunks")
    pages_l = read("web_pages")
    evidence_l = read("evidence")
    claims_v2_l = read("claims_v2")
    claims_legacy_l = read("claims")
    citation_l = read("citation_checks")
    reports_l = read("reports")
    statements_l = read("report_statements")

    claims_l = claims_v2_l if claims_v2_l else claims_legacy_l
    claim_artifact = "claims_v2" if claims_v2_l else ("claims" if claims_legacy_l else None)

    sources = id_map(sources_l)
    chunks = id_map(chunks_l)
    pages = id_map(pages_l)
    evidence = id_map(evidence_l)
    claims = id_map(claims_l)
    citations = id_map(citation_l)
    reports = id_map(reports_l)
    statements = id_map(statements_l)

    citation_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in citation_l:
        claim_id = str(item.get("claim_id") or "").strip()
        if claim_id:
            citation_by_claim.setdefault(claim_id, []).append(item)

    # Evidence -> Source / Chunk / Quote
    evidence_details = []
    ev_source_ok = 0
    ev_url_ok = 0
    chunk_checkable = 0
    chunk_ok = 0
    quote_checkable = 0
    quote_ok = 0

    valid_evidence_ids: set[str] = set()
    valid_evidence_with_chunk_ids: set[str] = set()

    for eid, ev in evidence.items():
        sid = str(ev.get("source_id") or "").strip()
        source_exists = bool(sid and sid in sources)
        if source_exists:
            ev_source_ok += 1

        url_ok = bool(source_exists and source_url(sources[sid]))
        if url_ok:
            ev_url_ok += 1

        direct_chunk = evidence_chunk_id(ev)
        resolved_chunk_id, chunk_method = resolve_evidence_chunk(ev, chunks)

        if direct_chunk or chunks:
            # 有明确 chunk 引用，或任务本身是 chunk-aware 当前 Schema，才纳入 chunk 检查。
            chunk_checkable += 1
            if resolved_chunk_id:
                chunk_ok += 1

        quote = evidence_quote(ev)
        quote_match: bool | None = None
        if quote and resolved_chunk_id and resolved_chunk_id in chunks:
            quote_checkable += 1
            quote_match = quote in norm_text(chunks[resolved_chunk_id].get("text"))
            if quote_match:
                quote_ok += 1

        if source_exists and url_ok:
            valid_evidence_ids.add(eid)
        if source_exists and url_ok and resolved_chunk_id:
            valid_evidence_with_chunk_ids.add(eid)

        evidence_details.append({
            "evidence_id": eid,
            "source_id": sid or None,
            "source_ref_valid": source_exists,
            "source_url_present": url_ok,
            "chunk_id": resolved_chunk_id,
            "chunk_resolution": chunk_method,
            "quote_checkable": quote_match is not None,
            "quote_match": quote_match,
        })

    # Claim -> Evidence / CitationCheck
    claim_details = []
    claim_evidence_ref_total = 0
    claim_evidence_ref_ok = 0
    claim_has_valid_evidence = 0
    claim_has_valid_chunk_evidence = 0
    claim_has_citation = 0
    claim_citation_nonblocking = 0
    evidence_referenced_by_claim: set[str] = set()

    for cid, claim in claims.items():
        eids = as_list(claim.get("evidence_ids"))
        refs_ok = [eid for eid in eids if eid in evidence]
        valid = [eid for eid in eids if eid in valid_evidence_ids]
        valid_chunk = [eid for eid in eids if eid in valid_evidence_with_chunk_ids]
        evidence_referenced_by_claim.update(eid for eid in eids if eid in evidence)

        claim_evidence_ref_total += len(eids)
        claim_evidence_ref_ok += len(refs_ok)
        if valid:
            claim_has_valid_evidence += 1
        if valid_chunk:
            claim_has_valid_chunk_evidence += 1

        checks = citation_by_claim.get(cid, [])
        if checks:
            claim_has_citation += 1
        statuses = {
            str(item.get("status") or "").strip().lower()
            for item in checks
            if str(item.get("status") or "").strip()
        }
        if checks and not (statuses & INVALID_CITATION_STATUSES):
            claim_citation_nonblocking += 1

        claim_details.append({
            "claim_id": cid,
            "evidence_ids": eids,
            "resolved_evidence_ids": refs_ok,
            "valid_source_evidence_ids": valid,
            "valid_chunk_evidence_ids": valid_chunk,
            "citation_check_ids": [
                str(item.get("id") or "") for item in checks if item.get("id")
            ],
            "citation_statuses": sorted(statuses),
        })

    # CitationCheck -> Claim
    citation_claim_ok = sum(
        1
        for item in citation_l
        if str(item.get("claim_id") or "").strip() in claims
    )

    # Report -> Claim refs
    report_claim_ref_total = 0
    report_claim_ref_ok = 0
    for report in reports_l:
        ids = as_list(report.get("claim_ids"))
        report_claim_ref_total += len(ids)
        report_claim_ref_ok += sum(1 for cid in ids if cid in claims)

    # ReportStatement -> Report / Claim / Evidence
    stmt_details = []
    claim_statement_count = 0
    claim_statement_ref_ok = 0
    statement_evidence_ref_total = 0
    statement_evidence_ref_ok = 0
    statement_full_source_chain = 0
    statement_full_chunk_chain = 0
    statements_with_unknown_refs = 0
    claims_referenced_by_statement: set[str] = set()

    for sid, stmt in statements.items():
        report_id = str(stmt.get("report_id") or "").strip()
        report_ok = bool(report_id and report_id in reports)

        claim_ids = as_list(stmt.get("claim_ids"))
        statement_eids = as_list(stmt.get("evidence_ids"))
        resolved_claims = [cid for cid in claim_ids if cid in claims]
        resolved_stmt_evidence = [eid for eid in statement_eids if eid in evidence]
        claims_referenced_by_statement.update(resolved_claims)

        kind = str(stmt.get("statement_kind") or "").strip().lower()
        is_claim_stmt = kind == "claim" or bool(claim_ids)

        if is_claim_stmt:
            claim_statement_count += 1
            if claim_ids and len(resolved_claims) == len(claim_ids):
                claim_statement_ref_ok += 1

        statement_evidence_ref_total += len(statement_eids)
        statement_evidence_ref_ok += len(resolved_stmt_evidence)

        unknown_claims = sorted(set(claim_ids) - set(resolved_claims))
        unknown_evidence = sorted(set(statement_eids) - set(resolved_stmt_evidence))
        if unknown_claims or unknown_evidence or (report_id and not report_ok):
            statements_with_unknown_refs += 1

        source_chain = False
        chunk_chain = False
        for cid in resolved_claims:
            claim = claims[cid]
            claim_eids = as_list(claim.get("evidence_ids"))
            stmt_filter = set(statement_eids) if statement_eids else set(claim_eids)
            candidate_eids = [eid for eid in claim_eids if eid in stmt_filter]

            has_check = bool(citation_by_claim.get(cid))
            if not has_check:
                continue

            if any(eid in valid_evidence_ids for eid in candidate_eids):
                source_chain = True
            if any(eid in valid_evidence_with_chunk_ids for eid in candidate_eids):
                chunk_chain = True

        if is_claim_stmt and report_ok and source_chain:
            statement_full_source_chain += 1
        if is_claim_stmt and report_ok and chunk_chain:
            statement_full_chunk_chain += 1

        stmt_details.append({
            "statement_id": sid,
            "statement_kind": kind or None,
            "report_id": report_id or None,
            "report_ref_valid": report_ok,
            "claim_ids": claim_ids,
            "resolved_claim_ids": resolved_claims,
            "evidence_ids": statement_eids,
            "resolved_evidence_ids": resolved_stmt_evidence,
            "unknown_claim_ids": unknown_claims,
            "unknown_evidence_ids": unknown_evidence,
            "full_source_chain": bool(is_claim_stmt and report_ok and source_chain),
            "full_chunk_chain": bool(is_claim_stmt and report_ok and chunk_chain),
        })

    orphan_evidence = sorted(set(evidence) - evidence_referenced_by_claim)
    orphan_claims = sorted(set(claims) - claims_referenced_by_statement)

    metrics = {
        "evidence_source_ref_rate": ratio(ev_source_ok, len(evidence)),
        "evidence_source_url_rate": ratio(ev_url_ok, len(evidence)),
        "evidence_chunk_resolution_rate": ratio(chunk_ok, chunk_checkable),
        "exact_quote_match_rate": ratio(quote_ok, quote_checkable),
        "claim_evidence_ref_rate": ratio(claim_evidence_ref_ok, claim_evidence_ref_total),
        "claim_with_valid_source_evidence_rate": ratio(claim_has_valid_evidence, len(claims)),
        "claim_with_valid_chunk_evidence_rate": ratio(claim_has_valid_chunk_evidence, len(claims)),
        "claim_with_citation_check_rate": ratio(claim_has_citation, len(claims)),
        "claim_with_nonblocking_citation_rate": ratio(claim_citation_nonblocking, len(claims)),
        "citation_check_claim_ref_rate": ratio(citation_claim_ok, len(citation_l)),
        "report_claim_ref_rate": ratio(report_claim_ref_ok, report_claim_ref_total),
        "report_statement_claim_ref_rate": ratio(claim_statement_ref_ok, claim_statement_count),
        "report_statement_evidence_ref_rate": ratio(
            statement_evidence_ref_ok, statement_evidence_ref_total
        ),
        "report_statement_full_source_traceability_rate": ratio(
            statement_full_source_chain, claim_statement_count
        ),
        "report_statement_full_chunk_traceability_rate": ratio(
            statement_full_chunk_chain, claim_statement_count
        ),
    }

    counts = {
        "sources": len(sources),
        "source_chunks": len(chunks),
        "web_pages": len(pages),
        "evidence": len(evidence),
        "claims": len(claims),
        "citation_checks": len(citation_l),
        "reports": len(reports),
        "report_statements": len(statements),
        "claim_statements": claim_statement_count,
        "chunk_checkable_evidence": chunk_checkable,
        "quote_checkable_evidence": quote_checkable,
        "orphan_evidence": len(orphan_evidence),
        "orphan_claims": len(orphan_claims),
        "statements_with_unknown_refs": statements_with_unknown_refs,
    }

    # 核心 verdict 只基于当前可确定的结构链，不把 citation_status=blocked 当成“链断裂”。
    core_rates = [
        metrics["evidence_source_ref_rate"],
        metrics["claim_evidence_ref_rate"],
        metrics["citation_check_claim_ref_rate"],
        metrics["report_claim_ref_rate"],
        metrics["report_statement_claim_ref_rate"],
        metrics["report_statement_full_source_traceability_rate"],
    ]
    available_core = [x for x in core_rates if x is not None]
    if errors:
        verdict = "PARTIAL"
    elif available_core and all(x == 1.0 for x in available_core):
        verdict = "PASS"
    elif available_core:
        verdict = "PARTIAL"
    else:
        verdict = "INSUFFICIENT_DATA"

    return {
        "task_id": task_dir.name,
        "claim_artifact": claim_artifact,
        "counts": counts,
        "metrics": metrics,
        "verdict": verdict,
        "orphan_evidence_ids": orphan_evidence,
        "orphan_claim_ids": orphan_claims,
        "scan_errors": errors,
        "evidence_details": evidence_details,
        "claim_details": claim_details,
        "statement_details": stmt_details,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = sorted({
        key for result in results for key in result.get("metrics", {})
    })
    macro = {}
    for name in metric_names:
        values = [
            result["metrics"].get(name)
            for result in results
            if result["metrics"].get(name) is not None
        ]
        macro[name] = round(statistics.mean(values), 4) if values else None

    return {
        "task_count": len(results),
        "verdicts": {
            status: sum(1 for r in results if r["verdict"] == status)
            for status in ("PASS", "PARTIAL", "INSUFFICIENT_DATA")
        },
        "macro_average": macro,
        "totals": {
            field: sum(r["counts"].get(field, 0) for r in results)
            for field in (
                "sources",
                "source_chunks",
                "evidence",
                "claims",
                "citation_checks",
                "reports",
                "report_statements",
                "claim_statements",
                "orphan_evidence",
                "orphan_claims",
                "statements_with_unknown_refs",
            )
        },
    }


def write_report(path: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# Traceability Eval V1",
        "",
        "本评测为纯本地 Artifact 引用完整性检查，不调用 LLM/Search/MCP。",
        "",
        "## Overall",
        "",
        f"- tasks: **{summary['task_count']}**",
        f"- verdicts: `{summary['verdicts']}`",
        f"- totals: `{summary['totals']}`",
        "",
        "### Macro average",
        "",
    ]
    for name, value in summary["macro_average"].items():
        lines.append(f"- {name}: **{pct(value)}**")

    lines.extend(["", "## Per task", ""])
    for result in results:
        m = result["metrics"]
        c = result["counts"]
        lines.extend([
            f"### {result['task_id']} — {result['verdict']}",
            "",
            f"- Artifact counts: Source={c['sources']}, Chunk={c['source_chunks']}, "
            f"Evidence={c['evidence']}, Claim={c['claims']}, "
            f"CitationCheck={c['citation_checks']}, Statement={c['report_statements']}",
            f"- Evidence→Source: **{pct(m['evidence_source_ref_rate'])}**",
            f"- Evidence→Chunk: **{pct(m['evidence_chunk_resolution_rate'])}**",
            f"- Exact quote/snippet match: **{pct(m['exact_quote_match_rate'])}** "
            f"(checkable={c['quote_checkable_evidence']})",
            f"- Claim→Evidence: **{pct(m['claim_evidence_ref_rate'])}**",
            f"- Claim→CitationCheck: **{pct(m['claim_with_citation_check_rate'])}**",
            f"- CitationCheck→Claim: **{pct(m['citation_check_claim_ref_rate'])}**",
            f"- Report→Claim: **{pct(m['report_claim_ref_rate'])}**",
            f"- ReportStatement→Claim: **{pct(m['report_statement_claim_ref_rate'])}**",
            f"- Full source traceability: **{pct(m['report_statement_full_source_traceability_rate'])}**",
            f"- Full chunk traceability: **{pct(m['report_statement_full_chunk_traceability_rate'])}**",
            f"- orphan evidence / claims / broken statements: "
            f"**{c['orphan_evidence']} / {c['orphan_claims']} / {c['statements_with_unknown_refs']}**",
            "",
        ])

    lines.extend([
        "## Metric definitions",
        "",
        "- Full source traceability：claim 类型 ReportStatement 能解析到有效 Report、Claim、CitationCheck、Evidence，且 Evidence 的 Source 存在并带 URL。",
        "- Full chunk traceability：在 Full source traceability 基础上，Evidence 还能解析到 SourceChunk。",
        "- Exact quote/snippet match：仅对既有 Evidence quote/snippet 且能定位 Chunk 的样本计算；不可检查样本不计入分母。",
        "- `citation_status=blocked/weak` 不等同于引用链断裂；本脚本把“结构可追溯性”和“证据质量判定”分开。",
        "- 老 Schema 缺失 SourceChunk 时不伪造失败；对应 Chunk/quote 指标可能为 N/A。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_self_test() -> None:
    root = Path(tempfile.mkdtemp())
    good = root / "task_good"
    good.mkdir()
    artifacts = {
        "sources": [{"id": "s1", "url": "https://example.test/a"}],
        "source_chunks": [{
            "id": "ch1", "source_id": "s1",
            "source_text_start": 0, "source_text_end": 20,
            "text": "Alpha product supports API."
        }],
        "evidence": [{
            "id": "e1", "source_id": "s1", "chunk_id": "ch1",
            "snippet": "supports API", "source_text_start": 14, "source_text_end": 26
        }],
        "claims_v2": [{"id": "c1", "evidence_ids": ["e1"]}],
        "citation_checks": [{"id": "cc1", "claim_id": "c1", "status": "supported"}],
        "reports": [{"id": "r1", "claim_ids": ["c1"]}],
        "report_statements": [{
            "id": "st1", "report_id": "r1", "statement_kind": "claim",
            "claim_ids": ["c1"], "evidence_ids": ["e1"]
        }],
    }
    for name, value in artifacts.items():
        (good / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

    result = evaluate_task(good)
    assert result["metrics"]["evidence_source_ref_rate"] == 1.0
    assert result["metrics"]["claim_evidence_ref_rate"] == 1.0
    assert result["metrics"]["report_statement_full_source_traceability_rate"] == 1.0

    broken = root / "task_broken"
    broken.mkdir()
    broken_artifacts = {
        "sources": [{"id": "s1", "url": "https://example.test/a"}],
        "evidence": [{"id": "e1", "source_id": "missing", "snippet": "x"}],
        "claims_v2": [{"id": "c1", "evidence_ids": ["missing_e"]}],
        "citation_checks": [{"id": "cc1", "claim_id": "missing_c", "status": "supported"}],
        "reports": [{"id": "r1", "claim_ids": ["missing_c"]}],
        "report_statements": [{
            "id": "st1", "report_id": "r1", "statement_kind": "claim",
            "claim_ids": ["missing_c"], "evidence_ids": ["missing_e"]
        }],
    }
    for name, value in broken_artifacts.items():
        (broken / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

    bad = evaluate_task(broken)
    assert bad["metrics"]["evidence_source_ref_rate"] == 0.0
    assert bad["metrics"]["citation_check_claim_ref_rate"] == 0.0
    assert bad["metrics"]["report_statement_full_source_traceability_rate"] == 0.0
    print("SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline deterministic Traceability Eval for competitive-intel-agents."
    )
    script_path = Path(__file__).resolve()
    backend = script_path.parent
    if backend.name in {"scripts", "eval"}:
        backend = backend.parent

    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=backend / "app" / "data" / "runs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=backend / "eval_outputs" / "traceability",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Repeatable. If omitted, evaluate the three recommended real tasks.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate every task with sources/evidence/claims/report/report_statements.",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    runs_dir = args.runs_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not runs_dir.exists():
        raise SystemExit(f"runs dir not found: {runs_dir}")

    if args.all:
        task_ids = []
        for task_dir in sorted(runs_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            required = [
                task_dir / "sources.json",
                task_dir / "evidence.json",
                task_dir / "reports.json",
                task_dir / "report_statements.json",
            ]
            claim_ok = (
                (task_dir / "claims_v2.json").exists()
                or (task_dir / "claims.json").exists()
            )
            if all(p.exists() for p in required) and claim_ok:
                task_ids.append(task_dir.name)
    else:
        task_ids = args.task_ids or DEFAULT_TASKS

    results = []
    missing = []
    for task_id in task_ids:
        task_dir = runs_dir / task_id
        if not task_dir.exists():
            missing.append(task_id)
            continue
        results.append(evaluate_task(task_dir))

    if not results:
        raise SystemExit(
            "No evaluable tasks found. "
            f"Missing requested tasks: {', '.join(missing) if missing else 'none'}"
        )

    summary = aggregate(results)
    summary["missing_requested_tasks"] = missing

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "traceability_eval.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "traceability_eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(output_dir / "TRACEABILITY_EVAL.md", results, summary)

    print(f"Tasks evaluated: {len(results)}")
    if missing:
        print("Missing tasks:", ", ".join(missing))
    for result in results:
        m = result["metrics"]
        print(
            f"{result['task_id']}: {result['verdict']} | "
            f"full_source={pct(m['report_statement_full_source_traceability_rate'])} | "
            f"full_chunk={pct(m['report_statement_full_chunk_traceability_rate'])} | "
            f"quote_match={pct(m['exact_quote_match_rate'])}"
        )
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
