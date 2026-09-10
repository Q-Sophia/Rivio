from __future__ import annotations

import hashlib
import json
import string
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.schemas import DimensionDefinition, FrameworkDefinition


@dataclass(frozen=True)
class FrameworkSourceDocument:
    framework_id: str
    version: str
    status: str
    payload: dict[str, Any]
    content_hash: str


class FrameworkRegistryBackend(Protocol):
    """Storage boundary implemented by YAML now and replaceable by MySQL later."""

    def default_version(self, framework_id: str) -> str: ...

    def load_document(
        self,
        framework_id: str,
        version: str,
    ) -> FrameworkSourceDocument: ...


class YamlFrameworkRegistryBackend:
    """The only production component allowed to read framework YAML files."""

    def __init__(self, root_dir: Path | str | None = None):
        self.root_dir = Path(
            root_dir or Path(__file__).resolve().parent
        ).resolve()
        registry_path = self.root_dir / "registry.json"
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        entries = payload.get("frameworks", [])
        self._entries = {str(item["id"]): item for item in entries}
        if len(self._entries) != len(entries):
            raise ValueError("Framework registry 存在重复 framework id")

    def default_version(self, framework_id: str) -> str:
        entry = self._framework_entry(framework_id)
        version = str(entry.get("default_version") or "").strip()
        if not version:
            raise ValueError(f"Framework {framework_id} 未配置 default_version")
        return version

    def load_document(
        self,
        framework_id: str,
        version: str,
    ) -> FrameworkSourceDocument:
        entry = self._framework_entry(framework_id)
        versions = entry.get("versions", [])
        version_entry = next(
            (
                item
                for item in versions
                if str(item.get("version") or "") == version
            ),
            None,
        )
        if version_entry is None:
            raise KeyError(f"Framework 未注册: {framework_id}@{version}")
        path = (self.root_dir / str(version_entry.get("path") or "")).resolve()
        if self.root_dir != path and self.root_dir not in path.parents:
            raise ValueError("Framework path 超出注册表目录")
        raw = path.read_bytes()
        payload = yaml.safe_load(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Framework YAML 顶层必须是对象")
        return FrameworkSourceDocument(
            framework_id=framework_id,
            version=version,
            status=str(version_entry.get("status") or ""),
            payload=payload,
            content_hash=hashlib.sha256(raw).hexdigest(),
        )

    def _framework_entry(self, framework_id: str) -> dict[str, Any]:
        try:
            return self._entries[framework_id]
        except KeyError as exc:
            raise KeyError(f"Framework 未注册: {framework_id}") from exc


class FrameworkRegistry:
    """Validated, storage-agnostic interface used by application code."""

    _ALLOWED_TEMPLATE_FIELDS = {
        "competitor",
        "dimension_label",
        "decision_question",
    }

    def __init__(self, backend: FrameworkRegistryBackend | None = None):
        self.backend = backend or YamlFrameworkRegistryBackend()
        self._cache: dict[tuple[str, str], FrameworkDefinition] = {}
        self._lock = threading.RLock()

    def load_framework(
        self,
        framework_id: str,
        version: str,
    ) -> FrameworkDefinition:
        key = (str(framework_id).strip(), str(version).strip())
        if not all(key):
            raise ValueError("framework_id 和 version 不能为空")
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached.model_copy(deep=True)
            document = self.backend.load_document(*key)
            if document.status != "enabled":
                raise ValueError(
                    f"Framework {key[0]}@{key[1]} 状态为 {document.status}，不可用于规划"
                )
            payload = dict(document.payload)
            if payload.get("framework_id") != key[0]:
                raise ValueError("Framework YAML framework_id 与注册表不一致")
            if str(payload.get("version") or "") != key[1]:
                raise ValueError("Framework YAML version 与注册表不一致")
            definition = FrameworkDefinition.model_validate(
                {**payload, "content_hash": document.content_hash}
            )
            self._validate_templates(definition)
            self._cache[key] = definition
            return definition.model_copy(deep=True)

    def get_dimension(
        self,
        framework_id: str,
        dimension_id: str,
    ) -> DimensionDefinition:
        version = self.backend.default_version(framework_id)
        framework = self.load_framework(framework_id, version)
        for dimension in framework.dimensions:
            if dimension.dimension_id == dimension_id:
                return dimension.model_copy(deep=True)
        raise KeyError(
            f"Framework dimension 未注册: {framework_id}@{version}/{dimension_id}"
        )

    @classmethod
    def _validate_templates(cls, framework: FrameworkDefinition) -> None:
        formatter = string.Formatter()
        for dimension in framework.dimensions:
            values = [
                dimension.objective_template,
                *dimension.research_questions,
                *dimension.query_templates,
            ]
            for template in values:
                fields = {
                    field_name
                    for _, field_name, _, _ in formatter.parse(template)
                    if field_name
                }
                unsupported = sorted(fields - cls._ALLOWED_TEMPLATE_FIELDS)
                if unsupported:
                    raise ValueError(
                        f"Framework {framework.framework_id}@{framework.version} "
                        f"维度 {dimension.dimension_id} 使用了不支持的模板字段: "
                        + ", ".join(unsupported)
                    )


_DEFAULT_REGISTRY: FrameworkRegistry | None = None
_DEFAULT_REGISTRY_LOCK = threading.Lock()


def get_framework_registry() -> FrameworkRegistry:
    global _DEFAULT_REGISTRY
    with _DEFAULT_REGISTRY_LOCK:
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = FrameworkRegistry()
        return _DEFAULT_REGISTRY


def load_framework(framework_id: str, version: str) -> FrameworkDefinition:
    return get_framework_registry().load_framework(framework_id, version)


def get_dimension(framework_id: str, dimension_id: str) -> DimensionDefinition:
    return get_framework_registry().get_dimension(framework_id, dimension_id)
