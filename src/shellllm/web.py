"""DuckDuckGo HTML search + a follow-the-link fetcher.

`search` returns the top-N result list (title, url, snippet). `fetch_url`
follows one of those links and returns the page as readable plain text,
so an LLM agent can drill into a result when the snippet isn't enough.

Both stick to the stdlib for HTTP and HTML — no extra deps. `fetch_url`
refuses non-http(s) schemes and private/loopback addresses (basic SSRF
guard), and caps the response size so a runaway page can't blow up the
agent's context.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import quote_plus, unquote, urlparse
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

# Hard caps for fetch_url.
FETCH_MAX_BYTES = 500_000
FETCH_MAX_CHARS = 8_000
FETCH_TIMEOUT = 10.0


class FetchError(RuntimeError):
    """Raised when fetch_url refuses or fails."""


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
    except (URLError, TimeoutError, OSError, UnicodeError) as exc:
        return f"(web_search failed: {exc.__class__.__name__}: {exc})"
    if not results:
        return "(no results)"
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet}")
    return "\n\n".join(parts)


# ─── fetch_url ──────────────────────────────────────────────────────────


# Tags whose contents are not user-visible text. Skipped wholesale.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "svg", "iframe", "template"})

# Tags that imply a line break in the rendered text.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "blockquote",
        "pre",
        "hr",
        "td",
        "th",
    }
)


class _TextExtractor(HTMLParser):
    """Render an HTML document as flat, readable text.

    Strips scripts and styles, inserts a newline for block-level tags, and
    collapses whitespace at the end. Good enough for an LLM to skim — not
    a substitute for a real readability extractor.
    """

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _check_url_safe(url: str) -> str:
    """Validate ``url`` and return its hostname. Raise FetchError on refusal.

    Rules:
      - scheme must be http or https
      - hostname must resolve to a public address (no loopback, private,
        link-local, multicast, reserved). Protects against the LLM being
        coaxed into hitting localhost services or RFC1918 networks.

    This is a best-effort check — DNS rebinding could swap the IP between
    the lookup and the connect. For a local-LLM hobbyist tool that's an
    acceptable gap; the realistic threat is the model itself, not a
    concurrent network attacker.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"only http(s) URLs allowed, got scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise FetchError("URL has no host")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"DNS lookup failed for {parsed.hostname}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise FetchError(f"refusing to fetch private/local address {addr}")
    return parsed.hostname


def fetch_url(
    url: str,
    *,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = FETCH_MAX_BYTES,
) -> str:
    """Fetch ``url`` and return its readable text content.

    HTML is reduced to plain text (scripts/styles stripped, block tags
    become newlines). Non-HTML bodies are returned as-is, decoded as
    UTF-8 with replacement. Raises FetchError on refusal or network
    failure.
    """
    _check_url_safe(url)
    req = Request(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})

    try:
        with urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "") or ""
            raw = r.read(max_bytes + 1)
    except (URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{exc.__class__.__name__}: {exc}") from exc

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")

    is_html = "html" in ctype.lower() or "<html" in text[:1024].lower()
    if is_html:
        parser = _TextExtractor()
        parser.feed(text)
        text = parser.text

    # Collapse blank-line runs and trim whitespace.
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if not blank and cleaned:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(ln)
            blank = False
    out = "\n".join(cleaned).strip()

    if truncated:
        out += f"\n\n(truncated at {max_bytes} bytes)"
    return out


def fetch_url_as_text(url: str, *, max_chars: int = FETCH_MAX_CHARS) -> str:
    """LLM-tool-friendly wrapper: never raises, always returns text."""
    try:
        body = fetch_url(url)
    except FetchError as exc:
        return f"(fetch_url failed: {exc})"
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n\n(truncated at {max_chars} chars)"
    return body
