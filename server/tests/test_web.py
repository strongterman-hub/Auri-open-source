from __future__ import annotations

import asyncio
import json

import httpx

from app.agent.tools import WebSearchTool
from app.web.client import (
    DuckDuckGoSearchClient,
    OpenWebSearchClient,
    SearchResult,
    _decode_ddg_url,
    extract_html_text,
    fetch_url_text,
)


def test_extract_html_text() -> None:
    html = (
        "<html><head><title>Example</title><style>.x{color:red}</style></head>"
        "<body><h1>Heading</h1><p>Hello <b>world</b>.</p></body></html>"
    )
    title, body = extract_html_text(html)
    assert title == "Example"
    assert "Heading" in body
    assert "Hello" in body
    assert "world" in body
    assert "color:red" not in body


def test_decode_ddg_url() -> None:
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=abc"
    assert _decode_ddg_url(href) == "https://example.com/a?b=1"
    assert _decode_ddg_url("https://example.com/plain") == "https://example.com/plain"


def test_duckduckgo_parse_results() -> None:
    html = (
        '<a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&rut=x">'
        "Example Title</a>"
        '<a class="result__snippet" href="https://example.com">A useful snippet.</a>'
    )
    results = DuckDuckGoSearchClient._parse_results(html)
    assert len(results) == 1
    assert results[0].title == "Example Title"
    assert results[0].url == "https://example.com"
    assert results[0].snippet == "A useful snippet."


def test_openwebsearch_parse_results() -> None:
    payload = {
        "status": "ok",
        "data": [
            {
                "title": "Example Search Result",
                "url": "https://example.com",
                "description": "Description text of the search result.",
                "source": "Bing",
                "engine": "bing",
            }
        ],
    }
    results = OpenWebSearchClient._parse_results(payload)
    assert len(results) == 1
    assert results[0].title == "Example Search Result"
    assert results[0].url == "https://example.com"
    assert results[0].snippet == "Description text of the search result."


def test_openwebsearch_search_with_mock_transport() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "data": [
                    {
                        "title": "Open Web Search",
                        "url": "https://example.com/open-web-search",
                        "description": "A useful snippet.",
                        "engine": "bing",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)

    async def run() -> list[SearchResult]:
        async with httpx.AsyncClient(transport=transport) as client:
            search_client = OpenWebSearchClient(
                "http://127.0.0.1:3000",
                engine="bing",
                client=client,
            )
            return await search_client.search("open web search", max_results=3)

    results = asyncio.run(run())
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:3000/search"
    assert captured["json"] == {
        "query": "open web search",
        "limit": 3,
        "engines": ["bing"],
    }
    assert results[0].title == "Open Web Search"
    assert results[0].url == "https://example.com/open-web-search"


def test_fetch_url_text_with_mock_transport() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><title>Page</title><body><p>Readable content.</p></body></html>",
        )

    transport = httpx.MockTransport(handler)

    async def run() -> dict[str, str]:
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_url_text("https://example.com/page", client=client)

    data = asyncio.run(run())
    assert data["title"] == "Page"
    assert "Readable content." in data["text"]


class FakeSearchClient:
    available = True

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"{query} result",
                url="https://example.com/result",
                snippet="A snippet.",
            )
        ]


def test_web_search_tool() -> None:
    tool = WebSearchTool(FakeSearchClient())
    payload = json.loads(asyncio.run(tool.execute(query="热点新闻", max_results=3)))
    assert payload["available"] is True
    assert payload["results"][0]["title"] == "热点新闻 result"
