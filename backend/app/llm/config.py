from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas import LLMMode, LLMProvider


OPENAI_BASE_URL = "https://api.openai.com/v1"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_environment(name: str) -> str:
    """Read process env first, then the Windows user env without exposing values."""
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            stored, _kind = winreg.QueryValueEx(key, name)
        return str(stored).strip()
    except (FileNotFoundError, OSError):
        return ""


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProvider = LLMProvider.MOCK
    model: str = "mock-structured-v1"
    mode: LLMMode = LLMMode.LLM_WITH_FALLBACK
    base_url: str = ""
    api_key_env: str = "LLM_API_KEY"
    timeout_seconds: int = 30
    max_tokens: int = 4000
    temperature: float = 0.2
    output_language: str = "zh-CN"
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    enable_real_calls: bool = False
    allow_insecure_http: bool = False
    api_style: str = "mock"
    structured_output_mode: str = "json_schema"
    thinking_mode: str = "provider_default"
    trust_env_proxy: bool = False

    @property
    def api_key(self) -> str:
        return _read_environment(self.api_key_env)

    @property
    def is_real_provider(self) -> bool:
        return self.provider in {LLMProvider.OPENAI, LLMProvider.COMPATIBLE}

    @property
    def responses_url(self) -> str:
        return self.base_url.rstrip("/") + "/responses"

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def real_call_readiness_errors(self) -> list[str]:
        if not self.is_real_provider:
            return []
        errors: list[str] = []
        if not self.enable_real_calls:
            errors.append("LLM_ENABLE_REAL_CALLS=false，真实模型调用保险尚未开启")
        if not self.model.strip():
            errors.append("LLM_MODEL 不能为空")
        if not self.base_url.strip():
            errors.append("LLM_BASE_URL 不能为空")
        if not self.api_key:
            errors.append(f"环境变量 {self.api_key_env} 尚未配置")
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme != "https"
            and not self.allow_insecure_http
        ):
            errors.append(
                "LLM_BASE_URL 必须使用 HTTPS；仅本地测试时可显式设置 "
                "LLM_ALLOW_INSECURE_HTTP=true"
            )
        return errors

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS 必须大于 0")
        if self.max_tokens <= 0:
            raise ValueError("LLM_MAX_TOKENS 必须大于 0")
        if self.max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES 不能小于 0")
        if self.retry_base_seconds < 0:
            raise ValueError("LLM_RETRY_BASE_SECONDS 不能小于 0")
        if self.api_style not in {"mock", "responses", "chat_completions"}:
            raise ValueError(
                "LLM_API_STYLE 必须是 mock、responses 或 chat_completions"
            )
        if self.structured_output_mode not in {"json_schema", "json_object"}:
            raise ValueError(
                "LLM_STRUCTURED_OUTPUT_MODE 必须是 json_schema 或 json_object"
            )
        if self.thinking_mode not in {"provider_default", "enabled", "disabled"}:
            raise ValueError(
                "LLM_THINKING_MODE 必须是 provider_default、enabled 或 disabled"
            )
        if self.provider == LLMProvider.MOCK and self.api_style != "mock":
            raise ValueError("provider=mock 时 LLM_API_STYLE 必须是 mock")
        if self.is_real_provider and self.api_style == "mock":
            raise ValueError("真实 Provider 不能使用 LLM_API_STYLE=mock")


def load_llm_config(*, mode: str | None = None) -> LLMConfig:
    provider = LLMProvider(os.environ.get("LLM_PROVIDER", "mock"))
    configured_mode = LLMMode(mode or os.environ.get("LLM_MODE", "llm_with_fallback"))
    default_model = "mock-structured-v1" if provider == LLMProvider.MOCK else ""
    if provider == LLMProvider.OPENAI:
        base_url = OPENAI_BASE_URL
        api_key_env = os.environ.get("LLM_API_KEY_ENV", "OPENAI_API_KEY")
    else:
        base_url = os.environ.get("LLM_BASE_URL", "")
        api_key_env = os.environ.get("LLM_API_KEY_ENV", "LLM_API_KEY")
    default_api_style = {
        LLMProvider.MOCK: "mock",
        LLMProvider.OPENAI: "responses",
        LLMProvider.COMPATIBLE: "chat_completions",
    }[provider]
    config = LLMConfig(
        provider=provider,
        model=os.environ.get("LLM_MODEL", default_model),
        mode=configured_mode,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=int(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "4000")),
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
        output_language=os.environ.get("LLM_OUTPUT_LANGUAGE", "zh-CN"),
        max_retries=int(os.environ.get("LLM_MAX_RETRIES", "2")),
        retry_base_seconds=float(os.environ.get("LLM_RETRY_BASE_SECONDS", "0.5")),
        enable_real_calls=_env_bool("LLM_ENABLE_REAL_CALLS", False),
        allow_insecure_http=_env_bool("LLM_ALLOW_INSECURE_HTTP", False),
        api_style=os.environ.get("LLM_API_STYLE", default_api_style),
        structured_output_mode=os.environ.get(
            "LLM_STRUCTURED_OUTPUT_MODE",
            "json_schema",
        ),
        thinking_mode=os.environ.get("LLM_THINKING_MODE", "provider_default"),
        trust_env_proxy=_env_bool("LLM_TRUST_ENV_PROXY", False),
    )
    config.validate()
    return config


def build_deepseek_compatible_config(
    *,
    env_prefix: str,
    default_model: str = "deepseek-v4-flash",
    default_timeout_seconds: int = 120,
    default_max_tokens: int = 4000,
    temperature: float = 0.2,
    max_retries: int = 0,
    retry_base_seconds: float = 1.0,
) -> LLMConfig:
    """Build the shared DeepSeek-compatible config used by production LLM stages."""

    normalized_prefix = env_prefix.strip().strip("_").upper()
    if not normalized_prefix:
        raise ValueError("DeepSeek config env_prefix 不能为空")
    key = lambda suffix: f"{normalized_prefix}_LLM_{suffix}"
    config = LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model=os.environ.get(key("MODEL"), default_model),
        mode=LLMMode.LLM,
        base_url=os.environ.get(
            key("BASE_URL"),
            "https://api.deepseek.com/v1",
        ),
        api_key_env=os.environ.get(
            key("API_KEY_ENV"),
            "DEEPSEEK_API_KEY",
        ),
        timeout_seconds=int(
            os.environ.get(key("TIMEOUT_SECONDS"), str(default_timeout_seconds))
        ),
        max_tokens=int(
            os.environ.get(key("MAX_TOKENS"), str(default_max_tokens))
        ),
        temperature=temperature,
        output_language="zh-CN",
        max_retries=int(
            os.environ.get(key("MAX_RETRIES"), str(max_retries))
        ),
        retry_base_seconds=float(
            os.environ.get(
                key("RETRY_BASE_SECONDS"),
                str(retry_base_seconds),
            )
        ),
        enable_real_calls=True,
        api_style="chat_completions",
        structured_output_mode="json_object",
        thinking_mode="disabled",
        trust_env_proxy=_env_bool(key("TRUST_ENV_PROXY"), False),
    )
    config.validate()
    return config
