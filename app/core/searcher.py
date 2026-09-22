"""Searcher — query → product URLs from Persian software sites."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HTTP_TIMEOUT_S: Final[float] = 20.0
_BROWSER_TIMEOUT_MS: Final[int] = 30_000


# --------------------------------------------------------------------------- #
# Site configuration
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Site:
    name: str
    home: str
    product_pattern: re.Pattern[str]
    excluded_paths: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    min_score: float = 10.0


SITES: Final[dict[str, Site]] = {
    "soft98": Site(
        name="soft98",
        home="https://soft98.ir",
        product_pattern=re.compile(
            r"^https?://(?:www\.)?soft98\.ir/"
            r"(?:[a-z0-9\-]+/)+"
            r"\d+-[^/?#]+\.html$",
            re.IGNORECASE,
        ),
        excluded_paths=("/learning/", "/tags/", "/forum/", "/android/"),
        excluded_keywords=("اندروید", "android"),
    ),
    "p30download": Site(
        name="p30download",
        home="https://p30download.ir",
        product_pattern=re.compile(
            r"^https?://(?:www\.)?p30download\.(?:ir|com)/"
            r"fa/entry/\d+/[^/?#]+/?$",
            re.IGNORECASE,
        ),
        excluded_paths=("/fa/tutorial/", "/fa/mobile/", "/fa/game/"),
        excluded_keywords=("اندروید", "android", "آموزش", "tutorial"),
    ),
    "yasdl": Site(
        name="yasdl",
        home="https://www.yasdl.com",
        product_pattern=re.compile(
            r"^https?://(?:www\.)?yasdl\.com/"
            r"\d+/[^/?#]+\.html$",
            re.IGNORECASE,
        ),
        excluded_paths=("/category/", "/tag/"),
        excluded_keywords=("اندروید", "android", "آموزش"),
    ),
}


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

_PERSIAN_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]+")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# کلماتی که در matching نقشی ندارند (نام شرکت، حروف ربط)
_STOP_WORDS: Final[frozenset[str]] = frozenset({
    "adobe", "microsoft", "google", "mozilla", "apple",
    "the", "and", "for", "with", "pro", "plus",
})


def prepare(raw: str) -> str:
    """Normalize: lowercase, strip Persian, punctuation, collapse whitespace."""
    text = raw.lower()
    text = _PERSIAN_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> set[str]:
    """Split normalized text into unique words, removing stop words."""
    return {t for t in text.split() if t and t not in _STOP_WORDS}


# --------------------------------------------------------------------------- #
# Scoring signals
# --------------------------------------------------------------------------- #

# کلماتی که نشان می‌دهند این یک محصول متفاوت است (نه فقط build متفاوت)
_PRODUCT_VARIANTS: Final[frozenset[str]] = frozenset({
    "elements",
    "express",
    "lightroom",
    "reader",
    "runtime",
    "redistributable",
    "plugin",
    "plugins",
    "addon",
    "addons",
    "extension",
    "panel",
})


def _position_score(query: str, title: str) -> float:
    """Where in the title does query appear? Earlier = better. Returns 0..1."""
    if not query or not title or query not in title:
        return 0.0
    idx = title.index(query)
    word_pos = title[:idx].count(" ")
    total = len(title.split())
    if total <= 1:
        return 1.0
    return max(0.0, 1.0 - word_pos / (total - 1))


def _recall(query: str, title: str) -> float:
    """Fraction of query tokens present in title tokens. Returns 0..1."""
    q = tokenize(query)
    t = tokenize(title)
    if not q:
        return 0.0
    return len(q & t) / len(q)


def _initials(query: str, title: str) -> float:
    """Query matches title's word initials (abbreviation). Returns 0..1."""
    q = query.replace(" ", "")
    words = title.split()
    if not q or len(words) < 2:
        return 0.0
    best = 0.0
    for n in range(2, min(len(words), 7) + 1):
        initials = "".join(w[0] for w in words[:n] if w)
        if q == initials:
            best = max(best, 1.0)
        elif len(initials) >= 2 and q.startswith(initials):
            best = max(best, 0.8)
        elif len(q) >= 2 and initials.startswith(q):
            best = max(best, 0.7)
    return best


W_POSITION: Final[float] = 45.0
W_RECALL: Final[float] = 45.0
W_INITIALS: Final[float] = 10.0
W_PRODUCT_VARIANT: Final[float] = 40.0


def score(query: str, title: str) -> float:
    """Weighted relevance in [0, 100].

    Signals:
        position  — how early the query appears in the title
        recall    — fraction of query tokens present in title
        initials  — abbreviation match (vscode ↔ visual studio code)

    Penalty:
        product-variant words in title that are not in query
        (elements, express, lightroom, plugin, ...) subtract 40 each.
    """
    q = prepare(query)
    t = prepare(title)

    if not q or not t:
        return 0.0

    s = (
        W_POSITION * _position_score(q, t)
        + W_RECALL * _recall(q, t)
        + W_INITIALS * _initials(q, t)
    )

    extra = tokenize(t) - tokenize(q)
    s -= W_PRODUCT_VARIANT * len(extra & _PRODUCT_VARIANTS)

    return max(s, 0.0)


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Result:
    score: float
    title: str
    url: str


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

async def _fetch_soft98(query: str) -> list[tuple[str, str]]:
    """Search soft98 via Playwright. Returns [(title, url), ...]."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                locale="fa-IR",
            )
            page = await context.new_page()
            page.set_default_timeout(_BROWSER_TIMEOUT_MS)

            await page.goto(
                "https://soft98.ir/?do=search",
                wait_until="domcontentloaded",
            )
            await page.evaluate(
                """
                (q) => {
                    document.querySelector('#searchinput').value = q;
                    document.querySelector('#fullsearch').submit();
                }
                """,
                query,
            )
            await page.wait_for_load_state("networkidle")

            anchors = await page.locator(
                "main#mbd article.cbdd h2.cbddt a.cbddtl"
            ).all()

            out: list[tuple[str, str]] = []
            for a in anchors:
                title = (await a.inner_text() or "").strip()
                href = await a.get_attribute("href")
                if title and href:
                    out.append((title, href))
            return out
        finally:
            await browser.close()


def _parse_anchors(
    html: str,
    selector: str,
) -> list[tuple[str, str]]:
    """Extract (title, absolute_url) pairs from HTML by CSS selector."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select(selector):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if title and href:
            out.append((title, href))
    return out


async def _fetch_p30download(
    client: httpx.AsyncClient,
    query: str,
) -> list[tuple[str, str]]:
    """GET p30download search results. Returns [(title, url), ...]."""
    url = (
        "https://p30download.ir/search.php"
        f"?search={quote_plus(query)}&x=0&y=0"
    )
    response = await client.get(url)
    response.raise_for_status()
    return _parse_anchors(response.text, "article.post header h2 a")


async def _fetch_yasdl(
    client: httpx.AsyncClient,
    query: str,
) -> list[tuple[str, str]]:
    """GET yasdl search results. Returns [(title, url), ...]."""
    url = f"https://www.yasdl.com/?s={quote_plus(query)}"
    response = await client.get(url)
    response.raise_for_status()
    return _parse_anchors(response.text, "article.post h2.post-title a")


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

def _filter(
    site: Site,
    candidates: list[tuple[str, str]],
    query: str,
) -> list[Result]:
    """Drop candidates that fail URL patterns or score threshold."""
    out: list[Result] = []
    for title, url in candidates:
        if any(p in url for p in site.excluded_paths):
            continue
        if not site.product_pattern.match(url):
            continue

        s = score(query, title)

        title_low = title.lower()
        if any(k in title_low for k in site.excluded_keywords):
            s -= 20.0

        if s < site.min_score:
            continue

        out.append(Result(score=s, title=title, url=url))

    out.sort(key=lambda r: r.score, reverse=True)
    return out

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def search(query: str) -> dict[str, list[Result]]:
    """Search all sites in parallel and return {site_name: [Result, ...]}."""
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        raw = await asyncio.gather(
            _fetch_soft98(query),
            _fetch_p30download(client, query),
            _fetch_yasdl(client, query),
            return_exceptions=True,
        )

    names: tuple[str, ...] = ("soft98", "p30download", "yasdl")
    out: dict[str, list[Result]] = {}
    for name, candidates in zip(names, raw):
        if isinstance(candidates, BaseException):
            out[name] = []
            continue
        out[name] = _filter(SITES[name], candidates, query)
    return out


# --------------------------------------------------------------------------- #
# Manual test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    async def main() -> None:
        grouped = await search("photoshop")
        for site, results in grouped.items():
            print(f"\n[{site}] {len(results)} result(s)")
            for r in results[:8]:
                print(f"  {r.score:6.1f}  {r.title}")

    asyncio.run(main())