"""Minimal DuckDuckGo HTML search. Stdlib-only, ≤3 results, snippet text only.

The agent never fetches a URL: it only sees the result list (title, url,
snippet). That keeps the question-mark tool genuinely read-only over the
web — the model can cite a link, but nothing pulls the page content.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote_plus, unquote
from urllib.request import Request, urlopen


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class _DDGParser(HTMLParser):
    """Scrape https://html.duckduckgo.com/html/ — three fields per result."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._mode: str | None = None
        self._cur_title: list[str] = []
        self._cur_url: str = ""
        self._cur_snippet: list[str] = []
        self._in_result = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        cls = attr.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._mode = "title"
            self._cur_title = []
            # DDG wraps the target URL inside the href param "uddg".
            href = attr.get("href") or ""
            self._cur_url = _extract_uddg(href)
            self._in_result = True
        elif tag == "a" and "result__snippet" in cls:
            self._mode = "snippet"
            self._cur_snippet = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._mode == "title":
            self._mode = None
        elif tag == "a" and self._mode == "snippet":
            self._mode = None
            if self._cur_title and self._cur_url:
                self.results.append(
                    SearchResult(
                        title="".join(self._cur_title).strip(),
                        url=self._cur_url,
                        snippet="".join(self._cur_snippet).strip(),
                    )
                )
                self._cur_title = []
                self._cur_url = ""
                self._cur_snippet = []
                self._in_result = False

    def handle_data(self, data: str) -> None:
        if self._mode == "title":
            self._cur_title.append(data)
        elif self._mode == "snippet":
            self._cur_snippet.append(data)


def _extract_uddg(href: str) -> str:
    # DDG redirects look like //duckduckgo.com/l/?uddg=<urlencoded>&rut=...
    if "uddg=" not in href:
        return href
    after = href.split("uddg=", 1)[1]
    target = after.split("&", 1)[0]
    return unquote(target)


def search(query: str, *, n: int = 3, timeout: float = 8.0) -> list[SearchResult]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
    parser = _DDGParser()
    parser.feed(body)
    return parser.results[:n]


def search_as_text(query: str, *, n: int = 3) -> str:
    """Return a plain-text rendering suitable for an LLM tool result."""
    try:
        results = search(query, n=n)
    except Exception as exc:  # network is best-effort
        return f"(web_search failed: {exc.__class__.__name__}: {exc})"
    if not results:
        return "(no results)"
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet}")
    return "\n\n".join(parts)
