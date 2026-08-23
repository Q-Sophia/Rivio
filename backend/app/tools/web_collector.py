from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.tools.browser_renderer import BrowserRenderer, build_browser_renderer_from_env


USER_AGENT = "CompetitiveIntelResearchBot/0.1 (+local educational project)"


class URLPolicyError(ValueError):
    pass


class _VisibleTextParser(HTMLParser):
    HIDDEN_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.title_depth = 0
        self.title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.HIDDEN_TAGS:
            self.hidden_depth += 1
        if tag == "title":
            self.title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        if tag == "title" and self.title_depth:
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        if self.title_depth:
            self.title_parts.append(value)
        if not self.hidden_depth:
            self.parts.append(value)

    def result(self) -> tuple[str, str]:
        return " ".join(self.title_parts).strip(), "\n".join(self.parts)


@dataclass(frozen=True)
class WebFetchResult:
    requested_url: str
    final_url: str
    status_code: int
    title: str
    text: str
    content_type: str
    content_hash: str
    render_mode: str = "http"
    browser_engine: str = ""


class URLSafetyPolicy:
    """Reject SSRF-prone URLs before every request and redirect."""

    def __init__(self, *, resolve_dns: bool = True):
        self.resolve_dns = resolve_dns

    def validate(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise URLPolicyError("只允许 http / https URL")
        if not parsed.hostname or parsed.username or parsed.password:
            raise URLPolicyError("URL 主机无效或包含凭据")
        if parsed.port not in {None, 80, 443}:
            raise URLPolicyError("只允许标准 HTTP/HTTPS 端口")
        host = parsed.hostname.casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise URLPolicyError("禁止访问本机或局域网主机")
        addresses: list[str] = []
        try:
            addresses.append(str(ipaddress.ip_address(host)))
        except ValueError:
            if self.resolve_dns:
                addresses.extend(
                    item[4][0]
                    for item in socket.getaddrinfo(host, parsed.port or 443)
                )
        for address in set(addresses):
            if not ipaddress.ip_address(address).is_global:
                raise URLPolicyError(f"禁止访问非公网地址：{address}")
        return url


class WebCollectorTool:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2_000_000,
        max_text_chars: int = 250_000,
        max_redirects: int = 4,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
        url_policy: URLSafetyPolicy | None = None,
        browser_renderer: BrowserRenderer | None = None,
        enable_browser_fallback: bool = True,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_text_chars = max_text_chars
        self.max_redirects = max_redirects
        self.respect_robots = respect_robots
        self.url_policy = url_policy or URLSafetyPolicy()
        self.browser_renderer = (
            browser_renderer
            if browser_renderer is not None
            else (build_browser_renderer_from_env() if enable_browser_fallback else None)
        )
        self.client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
        )
        self._robots_cache: dict[str, bool] = {}

    def fetch(self, url: str) -> WebFetchResult:
        requested_url = self.url_policy.validate(url)
        if self.respect_robots and not self._robots_allowed(requested_url):
            raise PermissionError("robots.txt 不允许采集该 URL")
        current = requested_url
        response: httpx.Response | None = None
        for _ in range(self.max_redirects + 1):
            self.url_policy.validate(current)
            response = self.client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise httpx.HTTPError("重定向响应缺少 Location")
                current = urljoin(current, location)
                continue
            break
        else:
            raise httpx.TooManyRedirects("网页重定向次数超过限制")
        if response is None:
            raise RuntimeError("网页没有返回响应")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"text/html", "application/xhtml+xml", "text/plain", ""}:
            raise ValueError(f"暂不支持的网页 Content-Type：{content_type}")
        if len(response.content) > self.max_bytes:
            raise ValueError(f"网页超过大小限制：{len(response.content)} bytes")

        title, normalized = self._extract_text(response.text, content_type)
        render_mode = "http"
        browser_engine = ""
        if len(normalized) < 40 and self.browser_renderer is not None:
            rendered = self.browser_renderer.render(str(response.url))
            title, normalized = self._extract_text(rendered.html, "text/html")
            render_mode = "browser"
            browser_engine = rendered.engine
        if len(normalized) < 40:
            raise ValueError(
                "网页正文过短，普通 HTTP 与 Browser Fallback（浏览器渲染回退）均未取得有效正文"
            )
        return WebFetchResult(
            requested_url=requested_url,
            final_url=str(response.url),
            status_code=response.status_code,
            title=title,
            text=normalized,
            content_type=content_type or "text/html",
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            render_mode=render_mode,
            browser_engine=browser_engine,
        )

    def close(self) -> None:
        self.client.close()

    def _extract_text(self, body: str, content_type: str) -> tuple[str, str]:
        if content_type == "text/plain":
            title, text = "", body
        else:
            parser = _VisibleTextParser()
            parser.feed(body)
            title, text = parser.result()
        normalized = "\n".join(
            line.strip() for line in text.splitlines() if line.strip()
        )
        return title, normalized[: self.max_text_chars]

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cache_key = f"{origin}|{parsed.path}"
        if cache_key in self._robots_cache:
            return self._robots_cache[cache_key]
        robots_url = f"{origin}/robots.txt"
        self.url_policy.validate(robots_url)
        try:
            response = self.client.get(robots_url)
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                allowed = parser.can_fetch(USER_AGENT, url)
            elif response.status_code in {401, 403}:
                allowed = False
            else:
                allowed = True
        except httpx.HTTPError:
            allowed = True
        self._robots_cache[cache_key] = allowed
        return allowed
