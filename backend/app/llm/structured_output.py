from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_FENCED_JSON_PATTERN = re.compile(
    r"```(?:json)?\s*(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class StructuredJSONDiagnostics:
    response_chars: int
    candidate_count: int
    starts_with_object: bool
    ends_with_object: bool
    parse_error: str


class StructuredJSONParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        raw_output_text: str,
        diagnostics: StructuredJSONDiagnostics,
    ):
        super().__init__(message)
        self.raw_output_text = raw_output_text
        self.response_chars = diagnostics.response_chars
        self.candidate_count = diagnostics.candidate_count
        self.starts_with_object = diagnostics.starts_with_object
        self.ends_with_object = diagnostics.ends_with_object
        self.parse_error = diagnostics.parse_error


def parse_structured_json_text(value: str) -> dict[str, Any]:
    """Extract one strict top-level JSON object without repairing its syntax."""

    raw = str(value or "")
    cleaned = raw.strip().lstrip("\ufeff").strip()
    starts_with_object = cleaned.startswith("{")
    ends_with_object = cleaned.endswith("}")
    if not cleaned:
        raise _parse_error(
            raw,
            candidate_count=0,
            starts_with_object=False,
            ends_with_object=False,
            errors=["响应为空"],
        )

    direct = _try_json(cleaned)
    if isinstance(direct, dict):
        return direct
    if direct is not None:
        raise _parse_error(
            raw,
            candidate_count=1,
            starts_with_object=starts_with_object,
            ends_with_object=ends_with_object,
            errors=["JSON 顶层必须是对象"],
        )

    candidates: list[str] = []
    fenced_spans: list[tuple[int, int]] = []
    for match in _FENCED_JSON_PATTERN.finditer(cleaned):
        fenced_spans.append(match.span())
        candidate = match.group(1).strip()
        if candidate:
            candidates.append(candidate)

    outside_fences = _replace_spans_with_spaces(cleaned, fenced_spans)
    first_object = outside_fences.find("{")
    first_array = outside_fences.find("[")
    if not (
        first_array >= 0
        and (first_object < 0 or first_array < first_object)
    ):
        candidates.extend(_balanced_json_objects(outside_fences))

    unique_candidates = list(dict.fromkeys(candidates))
    parsed_objects: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate in unique_candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(
                f"line={exc.lineno},column={exc.colno}: {exc.msg}"
            )
            continue
        if not isinstance(parsed, dict):
            errors.append("候选 JSON 顶层不是对象")
            continue
        if parsed not in parsed_objects:
            parsed_objects.append(parsed)

    if len(parsed_objects) == 1:
        return parsed_objects[0]
    if len(parsed_objects) > 1:
        errors.append("响应包含多个不同的 JSON 对象，无法确定唯一结构化输出")
    if not errors:
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as exc:
            errors.append(
                f"line={exc.lineno},column={exc.colno}: {exc.msg}"
            )
    raise _parse_error(
        raw,
        candidate_count=len(unique_candidates),
        starts_with_object=starts_with_object,
        ends_with_object=ends_with_object,
        errors=errors,
    )


def _try_json(value: str) -> Any | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _replace_spans_with_spaces(
    value: str, spans: list[tuple[int, int]]
) -> str:
    if not spans:
        return value
    characters = list(value)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _balanced_json_objects(value: str) -> list[str]:
    results: list[str] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(value):
        if start < 0:
            if character == "{":
                start = index
                depth = 1
                in_string = False
                escaped = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                results.append(value[start : index + 1].strip())
                start = -1
    return results


def _parse_error(
    raw: str,
    *,
    candidate_count: int,
    starts_with_object: bool,
    ends_with_object: bool,
    errors: list[str],
) -> StructuredJSONParseError:
    detail = " | ".join(dict.fromkeys(errors))[:600]
    diagnostics = StructuredJSONDiagnostics(
        response_chars=len(raw),
        candidate_count=candidate_count,
        starts_with_object=starts_with_object,
        ends_with_object=ends_with_object,
        parse_error=detail or "没有找到合法 JSON 对象",
    )
    return StructuredJSONParseError(
        (
            "模型输出文本不是有效 JSON 对象；"
            f"response_chars={diagnostics.response_chars}；"
            f"candidate_count={diagnostics.candidate_count}；"
            f"starts_with_object={diagnostics.starts_with_object}；"
            f"ends_with_object={diagnostics.ends_with_object}；"
            f"parse_error={diagnostics.parse_error}"
        ),
        raw_output_text=raw,
        diagnostics=diagnostics,
    )
