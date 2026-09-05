from __future__ import annotations

import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class WebSearchClient(ABC):
    """Provider-agnostic web search interface."""

    available: bool = True

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data
        else:
            self._parts.append(data)


def extract_html_text(html: str) -> tuple[str | None, str]:
    """Return ``(title, normalized_body)`` from an HTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    body = re.sub(r"\s+", " ", " ".join(parser._parts)).strip()
    return parser.title, body


def _decode_ddg_url(href: str) -> str:
    """Resolve DuckDuckGo's redirect wrapper back to the original URL."""
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or ""):
        query = parse_qs(parsed.query)
        uddg = query.get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


async def fetch_url_text(
    url: str,
    *,
    max_chars: int = 12000,
    timeout: float = 15.0,
    user_agent: str = "Auri/0.1",
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """Fetch a URL and return its readable ``title`` and truncated ``text``."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Only http(s) URLs can be fetched.")

    async def _get() -> httpx.Response:
        if client is not None:
            return await client.get(url)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as own_client:
            return await own_client.get(url)

    response = await _get()
    response.raise_for_status()
    title, body = extract_html_text(response.text)
    return {
        "url": str(response.url),
        "title": (title or "").strip(),
        "text": body[:max_chars],
    }


class DuckDuckGoSearchClient(WebSearchClient):
    """Uses DuckDuckGo's HTML endpoint; requires no API key."""

    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self._client = client

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        response = await self._get({"q": query})
        if response.status_code != 200:
            return []
        return self._parse_results(response.text)[:max_results]

    async def _get(self, params: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(self.endpoint, params=params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.get(self.endpoint, params=params)

    @staticmethod
    def _parse_results(html: str) -> list[SearchResult]:
        anchors = _AnchorCollector()
        anchors.feed(html)
        results: list[SearchResult] = []
        pending_title: str | None = None
        pending_url: str | None = None
        for href, classes, text in anchors.items:
            if "result__a" in classes:
                pending_title = text
                pending_url = _decode_ddg_url(href)
            elif "result__snippet" in classes and pending_title and pending_url:
                results.append(
                    SearchResult(title=pending_title, url=pending_url, snippet=text)
                )
                pending_title = None
                pending_url = None
        return results


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, list[str], str]] = []
        self._href: str | None = None
        self._classes: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        self._href = attributes.get("href")
        self._classes = (attributes.get("class") or "").split()
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.items.append((self._href, self._classes, " ".join(self._buffer).strip()))
            self._href = None
            self._classes = []
            self._buffer = []


class TavilySearchClient(WebSearchClient):
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._client = client

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {
            "query": query,
            "max_results": max(1, min(max_results, 10)),
            "include_answer": False,
        }
        response = await self._post(payload)
        response.raise_for_status()
        data = response.json()
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or ""),
            )
            for item in data.get("results", [])
            if item.get("url")
        ]

    async def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self._client is not None:
            return await self._client.post(self.endpoint, json=payload, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(self.endpoint, json=payload, headers=headers)


class OpenWebSearchClient(WebSearchClient):
    """Search through a local open-webSearch daemon (no API key)."""

    def __init__(
        self,
        base_url: str,
        *,
        engine: str = "bing",
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.engine = engine or "bing"
        self.timeout = timeout
        self._client = client

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {
            "query": query,
            "limit": max(1, min(int(max_results), 50)),
            "engines": [self.engine],
        }
        response = await self._post(payload)
        if response.status_code != 200:
            return []
        return self._parse_results(response.json())[:max_results]

    async def _post(self, payload: dict) -> httpx.Response:
        url = f"{self.base_url}/search"
        if self._client is not None:
            return await self._client.post(url, json=payload)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, json=payload)

    @staticmethod
    def _parse_results(payload: object) -> list[SearchResult]:
        """Parse either ``{status, data: [...]}`` or a raw list of results."""
        if isinstance(payload, list):
            data = payload
        elif isinstance(payload, dict) and payload.get("status") == "ok":
            data = payload.get("data")
        else:
            data = None

        if isinstance(data, dict):
            items = data.get("results") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(
                item.get("url")
                or item.get("link")
                or item.get("href")
                or ""
            ).strip()
            if not url:
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            snippet = str(
                item.get("description")
                or item.get("snippet")
                or item.get("content")
                or item.get("summary")
                or ""
            ).strip()
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results


class NullWebSearchClient(WebSearchClient):
    """No-op provider used when web search is not configured."""

    available = False

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return []


def build_web_search_client(settings) -> WebSearchClient:
    provider = (settings.web_search_provider or "none").lower()
    if provider == "tavily" and settings.tavily_api_key:
        return TavilySearchClient(
            settings.tavily_api_key,
            timeout=settings.web_search_timeout_seconds,
        )
    if provider in ("openwebsearch", "open-websearch", "open_web_search"):
        base_url = getattr(settings, "web_search_base_url", None)
        if base_url:
            return OpenWebSearchClient(
                base_url,
                engine=getattr(settings, "web_search_engine", "bing") or "bing",
                timeout=settings.web_search_timeout_seconds,
            )
    if provider in ("duckduckgo", "ddg"):
        return DuckDuckGoSearchClient(timeout=settings.web_search_timeout_seconds)
    return NullWebSearchClient()
