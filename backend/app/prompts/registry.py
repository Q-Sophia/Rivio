from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.schemas import AnalysisTask


@dataclass(frozen=True)
class PromptDefinition:
    prompt_id: str
    version: str
    status: str
    path: Path
    content: dict[str, Any]
    content_hash: str

    def build_runtime_prompt(self, task: AnalysisTask) -> str:
        template = str(self.content.get("task_prompt_template", ""))
        template = self._replace_task_tokens(template, task)
        sections = [
            str(self.content.get("purpose", "")),
            str(self.content.get("system_prompt", "")),
            template,
            yaml.safe_dump(
                {
                    "analysis_method": self.content.get("analysis_method", []),
                    "claim_policy": self.content.get("claim_policy", {}),
                    "claim_evidence_alignment": self.content.get(
                        "claim_evidence_alignment", {}
                    ),
                    "negative_constraints": self.content.get(
                        "negative_constraints", []
                    ),
                    "few_shot_examples": self.content.get(
                        "few_shot_examples", []
                    ),
                    "output_contract": self.content.get("output_contract", {}),
                    "quality_self_check": self.content.get(
                        "quality_self_check", ""
                    ),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
        ]
        return "\n\n".join(section.strip() for section in sections if section.strip())

    def build_writer_runtime_prompt(
        self,
        task: AnalysisTask,
        *,
        resolved_title: str,
    ) -> str:
        template = self._replace_task_tokens(
            str(self.content.get("task_prompt_template", "")),
            task,
        ).replace("{{ resolved_report_title }}", resolved_title)
        sections = [
            str(self.content.get("purpose", "")),
            str(self.content.get("system_prompt", "")),
            template,
            yaml.safe_dump(
                {
                    "report_method": self.content.get("report_method", []),
                    "title_policy": self.content.get("title_policy", {}),
                    "section_contract": self.content.get("section_contract", []),
                    "evidence_policy": self.content.get("evidence_policy", {}),
                    "negative_constraints": self.content.get(
                        "negative_constraints", []
                    ),
                    "few_shot_examples": self.content.get(
                        "few_shot_examples", []
                    ),
                    "output_contract": self.content.get("output_contract", {}),
                    "quality_self_check": self.content.get(
                        "quality_self_check", ""
                    ),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
        ]
        return "\n\n".join(section.strip() for section in sections if section.strip())

    def build_intent_runtime_prompt(self, *, request_text: str, draft_id: str) -> str:
        template = str(self.content.get("task_prompt_template", ""))
        template = template.replace("{{ request_text }}", request_text)
        template = template.replace("{{ draft_id }}", draft_id)
        sections = [
            str(self.content.get("purpose", "")),
            str(self.content.get("system_prompt", "")),
            template,
            yaml.safe_dump(
                {
                    "extraction_policy": self.content.get("extraction_policy", {}),
                    "research_mode_policy": self.content.get(
                        "research_mode_policy", []
                    ),
                    "clarification_policy": self.content.get(
                        "clarification_policy", {}
                    ),
                    "title_policy": self.content.get("title_policy", {}),
                    "negative_constraints": self.content.get("negative_constraints", []),
                    "output_contract": self.content.get("output_contract", {}),
                    "quality_self_check": self.content.get("quality_self_check", ""),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
        ]
        return "\n\n".join(section.strip() for section in sections if section.strip())

    @staticmethod
    def _replace_task_tokens(template: str, task: AnalysisTask) -> str:
        replacements = {
            "{{ analysis_task.query }}": task.query,
            "{{ analysis_task.industry }}": task.industry or "待确认",
            "{{ analysis_task.competitors }}": "、".join(task.competitors),
            "{{ analysis_task.focus_areas }}": "、".join(task.focus_areas),
            "{{ analysis_task.report_subject }}": task.report_subject or "待确认",
            "{{ analysis_task.preferred_title }}": task.preferred_title or "未指定",
            "{{ user_constraints }}": "未提供",
            "{{ research_brief }}": "由 AnalysisTask（分析任务）和输入证据初步构建",
        }
        for token, value in replacements.items():
            template = template.replace(token, value)
        return template


class PromptRegistry:
    """Load versioned prompt definitions without enabling candidates implicitly."""

    def __init__(self, root_dir: Path | str | None = None):
        self.root_dir = Path(root_dir or Path(__file__).resolve().parent).resolve()
        registry_path = self.root_dir / "registry.json"
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        self._entries = {
            str(item["id"]): item for item in payload.get("prompts", [])
        }

    def load(self, prompt_id: str, *, allow_candidate: bool = False) -> PromptDefinition:
        try:
            entry = self._entries[prompt_id]
        except KeyError as exc:
            raise KeyError(f"Prompt 未注册: {prompt_id}") from exc
        status = str(entry.get("status", ""))
        if status != "enabled" and not allow_candidate:
            raise ValueError(
                f"Prompt {prompt_id}@{entry.get('version')} 状态为 {status}，"
                "必须显式 allow_candidate=True 才能用于试验运行"
            )
        prompt_path = (self.root_dir / str(entry["path"])).resolve()
        if self.root_dir not in prompt_path.parents:
            raise ValueError("Prompt path 超出注册表目录")
        raw = prompt_path.read_bytes()
        content = yaml.safe_load(raw.decode("utf-8"))
        if content.get("prompt_id") != prompt_id:
            raise ValueError("Prompt 文件 prompt_id 与注册表不一致")
        if content.get("prompt_version") != entry.get("version"):
            raise ValueError("Prompt 文件版本与注册表不一致")
        return PromptDefinition(
            prompt_id=prompt_id,
            version=str(entry["version"]),
            status=status,
            path=prompt_path,
            content=content,
            content_hash=hashlib.sha256(raw).hexdigest(),
        )
