from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from app.schemas import (
    InformationNeed,
    ResearchAgentObservation,
    ResearchAgentRun,
    ResearchTask,
)


RESEARCH_ACTION_CONTEXT_TRACES_ARTIFACT = "research_action_context_traces"
RECENT_OBSERVATION_LIMIT = 4
VISIBLE_OBSERVED_TERM_LIMIT = 24

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_REDUNDANT_PAYLOAD_KEYS = {
    "schema_version",
    "metadata",
    "task_id",
    "research_task_id",
    "action_id",
    "created_at",
    "completed_at",
}
_SEARCH_RESULT_KEYS = (
    "title",
    "url",
    "snippet",
    "source_level",
    "source_type",
    "official_confidence",
    "freshness",
    "source_tool",
    "channel",
    "selected_for_collection",
    "rejection_reason",
)
_SEARCH_PAYLOAD_KEYS = (
    "query",
    "search_scope",
    "source_preference",
    "official_domains",
    "probable_official_domains",
    "result_count",
    "results",
    "source_breakdown",
    "supplemental_errors",
)
_READ_PAYLOAD_KEYS = (
    "source_id",
    "retrieval_run_id",
    "chunks",
)
_READ_CHUNK_KEYS = (
    "chunk_id",
    "source_id",
    "source_text_start",
    "source_text_end",
    "text",
)


@dataclass(frozen=True)
class ResearchActionContextView:
    """One read-only projection of persisted Research Agent state for the LLM."""

    artifacts: dict[str, list]
    context_mode: str
    section_chars: dict[str, int]
    section_estimated_tokens: dict[str, int]
    total_chars: int
    estimated_input_tokens: int
    observation_count: int
    candidate_count: int
    available_candidate_count: int
    evidence_count: int
    observed_term_count: int


class ResearchActionContextViewBuilder:
    """Build the LLM-visible view without mutating the complete runtime state."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        observed_term_limit: int = VISIBLE_OBSERVED_TERM_LIMIT,
        recent_observation_limit: int = RECENT_OBSERVATION_LIMIT,
    ):
        if observed_term_limit < 1:
            raise ValueError("observed_term_limit 必须大于 0")
        if recent_observation_limit < 1:
            raise ValueError("recent_observation_limit 必须大于 0")
        self.enabled = bool(enabled)
        self.observed_term_limit = observed_term_limit
        self.recent_observation_limit = recent_observation_limit

    def build(
        self,
        *,
        research_task: ResearchTask,
        information_need: InformationNeed | None,
        state: ResearchAgentRun,
        recent_observations: list[ResearchAgentObservation],
        mission_context: dict[str, Any] | None,
        available_artifacts: dict[str, list] | None = None,
    ) -> ResearchActionContextView:
        research_state = state.model_dump(mode="json")
        for field in (
            "attempted_queries",
            "visited_urls",
            "rejected_sources",
        ):
            research_state[field] = self._compact_history_summary(
                list(getattr(state, field))
            )

        visible_observations = list(recent_observations)[
            -self.recent_observation_limit:
        ]
        if self.enabled:
            research_state["observed_terms"] = self._compact_observed_terms(
                research_state.get("observed_terms", [])
            )
            observation_payload = [
                self._compact_observation(item)
                for item in visible_observations
            ]
            context_mode = "governed"
        else:
            observation_payload = [
                item.model_dump(mode="json")
                for item in visible_observations
            ]
            context_mode = "legacy"

        # Keep this list exactly aligned with the pre-R1 request. In particular,
        # research_source_candidates are intentionally not introduced here; that
        # is a separate behavior change and must not contaminate the context A/B.
        artifacts = {
            "research_task": [research_task.model_dump(mode="json")],
            "information_need": (
                [information_need.model_dump(mode="json")]
                if information_need is not None
                else []
            ),
            "research_state": [research_state],
            "recent_observations": observation_payload,
            "mission_context": [mission_context] if mission_context else [],
        }
        measured_sections = {
            **artifacts,
            # This diagnostic subsection intentionally overlaps with
            # research_state. It lets the A/B report attribute the largest
            # R1 reduction without changing the model-visible payload.
            "observed_terms": research_state.get("observed_terms", []),
        }
        section_chars = {
            key: len(self._serialize(value))
            for key, value in measured_sections.items()
        }
        section_estimated_tokens = {
            key: self._estimate_tokens(self._serialize(value))
            for key, value in measured_sections.items()
        }
        serialized = self._serialize(artifacts)
        evidence_ids = {
            str(item)
            for item in state.verified_evidence_ids
            if str(item)
        }
        if mission_context:
            evidence_ids.update(
                str(item.get("evidence_id") or "")
                for item in mission_context.get(
                    "related_verified_evidence", []
                )
                if item.get("evidence_id")
            )
        available_candidate_count = len(
            (available_artifacts or {}).get(
                "research_source_candidates", []
            )
        )
        return ResearchActionContextView(
            artifacts=artifacts,
            context_mode=context_mode,
            section_chars=section_chars,
            section_estimated_tokens=section_estimated_tokens,
            total_chars=len(serialized),
            estimated_input_tokens=self._estimate_tokens(serialized),
            observation_count=len(observation_payload),
            candidate_count=0,
            available_candidate_count=available_candidate_count,
            evidence_count=len(evidence_ids),
            observed_term_count=len(research_state.get("observed_terms", [])),
        )

    @staticmethod
    def _compact_history_summary(
        values: list[str],
        *,
        recent_limit: int = 3,
    ) -> dict[str, Any]:
        return {
            "count": len(values),
            "recent": list(values[-recent_limit:]),
        }

    def _compact_observed_terms(
        self,
        values: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        newest_unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in reversed(values):
            term = str(raw.get("term") or "").strip()
            identity = term.casefold()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            newest_unique.append(
                {
                    "term": term,
                    "discovered_from": str(
                        raw.get("discovered_from") or ""
                    ),
                }
            )
            if len(newest_unique) >= self.observed_term_limit:
                break
        newest_unique.reverse()
        return newest_unique

    def _compact_observation(
        self,
        observation: ResearchAgentObservation,
    ) -> dict[str, Any]:
        action = str(observation.action)
        return {
            "action": action,
            "status": observation.status,
            "summary": observation.summary,
            "payload": self._compact_observation_payload(
                action,
                observation.payload,
            ),
        }

    def _compact_observation_payload(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action == "SEARCH":
            compact: dict[str, Any] = {}
            for key in _SEARCH_PAYLOAD_KEYS:
                if key not in payload:
                    continue
                value = payload[key]
                if key == "results" and isinstance(value, list):
                    compact[key] = [
                        self._select_keys(item, _SEARCH_RESULT_KEYS)
                        for item in value
                        if isinstance(item, dict)
                    ]
                else:
                    compact[key] = self._strip_redundant(value)
            return self._drop_empty(compact)
        if action == "READ":
            compact = {}
            for key in _READ_PAYLOAD_KEYS:
                if key not in payload:
                    continue
                value = payload[key]
                if key == "chunks" and isinstance(value, list):
                    # Exact chunk text is intentionally retained so the next
                    # SUBMIT_EVIDENCE action can quote the source verbatim.
                    compact[key] = [
                        self._select_keys(item, _READ_CHUNK_KEYS)
                        for item in value
                        if isinstance(item, dict)
                    ]
                else:
                    compact[key] = self._strip_redundant(value)
            return self._drop_empty(compact)
        return self._drop_empty(self._strip_redundant(payload))

    def _strip_redundant(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_redundant(item)
                for key, item in value.items()
                if key not in _REDUNDANT_PAYLOAD_KEYS
            }
        if isinstance(value, list):
            return [self._strip_redundant(item) for item in value]
        return value

    def _select_keys(
        self,
        value: dict[str, Any],
        keys: tuple[str, ...],
    ) -> dict[str, Any]:
        return self._drop_empty(
            {
                key: self._strip_redundant(value[key])
                for key in keys
                if key in value
            }
        )

    def _drop_empty(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact = {
                key: self._drop_empty(item)
                for key, item in value.items()
            }
            return {
                key: item
                for key, item in compact.items()
                if item not in ("", [], {}, None)
            }
        if isinstance(value, list):
            return [
                item
                for raw in value
                if (item := self._drop_empty(raw))
                not in ("", [], {}, None)
            ]
        return value

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        if not value:
            return 0
        cjk_count = len(_CJK_PATTERN.findall(value))
        non_cjk_count = len(value) - cjk_count
        return cjk_count + math.ceil(non_cjk_count / 4)
