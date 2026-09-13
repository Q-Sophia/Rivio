from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


CASES_PATH = Path(__file__).with_name("eval_cases.json")


class EvalCase(BaseModel):
    id: str = Field(pattern=r"^case_\d{2}$")
    request: str = Field(min_length=10)
    category: str = Field(min_length=1)


def load_eval_cases(path: Path | str = CASES_PATH) -> list[EvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("eval_cases.json 必须是数组")
    cases = [EvalCase(**item) for item in payload]
    ids = [item.id for item in cases]
    if len(cases) != 5 or len(set(ids)) != len(ids):
        raise ValueError("RIVIO-EVAL-R1 必须包含 5 个唯一 case")
    return cases


def select_eval_cases(
    cases: list[EvalCase],
    *,
    case_id: str = "",
    limit: int | None = None,
) -> list[EvalCase]:
    selected = cases
    if case_id:
        selected = [item for item in cases if item.id == case_id]
        if not selected:
            raise ValueError(f"未知评测 case: {case_id}")
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit 必须大于等于 1")
        selected = selected[:limit]
    return selected
