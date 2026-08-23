from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class BrowserRenderResult:
    html: str
    engine: str


class BrowserRenderer(Protocol):
    def render(self, url: str) -> BrowserRenderResult: ...


class ChromiumHeadlessRenderer:
    """Render one public page in an isolated, disposable browser profile."""

    DEFAULT_EXECUTABLES = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        timeout_seconds: float = 35.0,
        render_budget_ms: int = 8_000,
        max_html_bytes: int = 4_000_000,
    ):
        configured = executable or os.getenv("BROWSER_EXECUTABLE", "").strip()
        self.executable = (
            Path(configured) if configured else self.find_installed_executable()
        )
        self.timeout_seconds = timeout_seconds
        self.render_budget_ms = render_budget_ms
        self.max_html_bytes = max_html_bytes

    @classmethod
    def find_installed_executable(cls) -> Path | None:
        return next((path for path in cls.DEFAULT_EXECUTABLES if path.is_file()), None)

    @property
    def available(self) -> bool:
        return self.executable is not None and self.executable.is_file()

    def render(self, url: str) -> BrowserRenderResult:
        if not self.available:
            raise RuntimeError(
                "没有找到 Edge/Chrome；可通过 BROWSER_EXECUTABLE 指定浏览器路径"
            )
        assert self.executable is not None
        with tempfile.TemporaryDirectory(prefix="competitive-intel-browser-") as profile:
            command = [
                str(self.executable),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-sync",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-features=TranslateUI,OptimizationHints",
                "--run-all-compositor-stages-before-draw",
                f"--user-data-dir={profile}",
                f"--timeout={self.render_budget_ms}",
                f"--virtual-time-budget={self.render_budget_ms}",
                "--dump-dom",
                url,
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                process = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                    creationflags=creationflags,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"浏览器渲染超过 {self.timeout_seconds:g} 秒"
                ) from exc
        if process.returncode != 0:
            error = process.stderr.decode("utf-8", errors="replace")[-1000:].strip()
            raise RuntimeError(f"浏览器渲染失败（exit={process.returncode}）：{error}")
        if len(process.stdout) > self.max_html_bytes:
            raise ValueError(
                f"浏览器渲染结果超过大小限制：{len(process.stdout)} bytes"
            )
        html = process.stdout.decode("utf-8", errors="replace").strip()
        if not html:
            raise ValueError("浏览器没有返回渲染后的 DOM")
        engine = "edge_headless" if "edge" in self.executable.name.lower() else "chrome_headless"
        return BrowserRenderResult(html=html, engine=engine)


def build_browser_renderer_from_env() -> ChromiumHeadlessRenderer | None:
    enabled = os.getenv("BROWSER_FALLBACK_ENABLED", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    renderer = ChromiumHeadlessRenderer()
    return renderer if renderer.available else None
