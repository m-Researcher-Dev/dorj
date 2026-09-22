"""Searcher — query → product URLs from Persian software sites."""

from __future__ import annotations

# import asyncio
import re
from dataclasses import dataclass
from typing import Final
from playwright.async_api import async_playwright
import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote_plus


@dataclass(frozen=True)
class Site:
    name: str
    home: str
    product_pattern: re.Pattern[str]
    excluded_paths: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    min_score: float = 10.0


@dataclass(frozen=True)
class Result:
    score: float
    title: str
    url: str


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
        excluded_keywords=("اندروید", "android", "آموزش", "tutorial"),
    ),
    "yasdl": Site(
        name="yasdl",
        home="https://www.yasdl.com",
        product_pattern=re.compile(
            r"^https?://(?:www\.)?yasdl\.com/" r"\d+/[^/?#]+\.html$",
            re.IGNORECASE,
        ),
        excluded_paths=("/category/", "/tag/"),
        excluded_keywords=("اندروید", "android", "آموزش", "tutorial"),
    ),
    "p30download": Site(
        name="p30download",
        home="https://p30download.ir",
        product_pattern=re.compile(
            r"^https?://(?:www\.)?p30download\.ir/" r"fa/entry/\d+/[^/?#]+/?$",
            re.IGNORECASE,
        ),
        excluded_paths=("/fa/tutorial/", "/fa/mobile/", "/fa/game/"),
        excluded_keywords=("اندروید", "android", "آموزش", "tutorial"),
    ),
}


_PERSIAN_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")
# _VOWELS_RE = re.compile(r"[aeiou\s]")

W_EXACT = 45.0
W_TOKENS = 25.0
W_INITIALS = 15.0
W_CONSONANTS = 10.0

_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BROWSER_TIMEOUT_MS: Final[int] = 30_000
_HTTP_TIMEOUT_S: Final[float] = 20.0


def prepare(raw: str) -> str:
    """Normalize: lowercase, strip Persian, punctuation, extra whitespace."""
    text = raw.lower()
    text = _PERSIAN_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> set[str]:
    """Split normalized text into unique words."""
    return set(text.split())


def _exact(query: str, title: str) -> float:
    """Query appears as substring of title. Returns 0..1."""
    if not query or not title:
        return 0.0
    if query == title:
        return 1.0
    if query not in title:
        return 0.0
    word_pos = title[: title.index(query)].count(" ")
    if word_pos == 0:
        return 0.95
    if word_pos <= 2:
        return 0.85
    return 0.6


def _tokens(query: str, title: str) -> float:
    """Fraction of query tokens present in title tokens. Returns 0..1."""
    q = tokenize(query)
    if not q:
        return 0.0
    t = tokenize(title)
    return len(q & t) / len(q)


def _initials(query: str, title: str) -> float:
    """Query matches title's word initials (abbreviation). Returns 0..1.

    Catches 'vscode' <-> 'visual studio code'.
    Tries every word-prefix of the title up to 6 words.
    """
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


def score(query: str, title: str) -> float:
    """Weighted relevance in [0, 100]."""
    q = prepare(query)
    t = prepare(title)
    return (
        W_EXACT * _exact(q, t) + W_TOKENS * _tokens(q, t) + W_INITIALS * _initials(q, t)
    )


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
                timeout=_BROWSER_TIMEOUT_MS,
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
            await page.wait_for_load_state("networkidle", timeout=_BROWSER_TIMEOUT_MS)
            anchors = await page.locator(
                "main#mbd article.cbdd h2.cbddt a.cbddtl"
            ).all()
            out = []
            for a in anchors:
                title = await a.inner_text()
                href = await a.get_attribute("href")
                if title and href:
                    out.append((title.strip(), href))
            return out
        finally:
            await browser.close()


async def _fetch_yasdl(client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
    """GET yasdl search results. Returns [(title, url), ...]."""
    url = f"https://www.yasdl.com/?s={quote_plus(query)}"
    response = await client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select("article.post h2.post-title a"):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if title and href:
            out.append((title, "".join(href)))
    return out


async def _fetch_p30download(
    client: httpx.AsyncClient,
    query: str,
) -> list[tuple[str, str]]:
    """GET p30download search results. Returns [(title, url), ...]."""
    url = f"https://p30download.ir/search.php?search={quote_plus(query)}&x=0&y=0"
    response = await client.get(url)
    response.raise_for_status

    soup = BeautifulSoup(response.text, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select("article.post header h2 a"):
        title = a.get_text(separator=" ", strip=True)
        href = a.get("href", "")
        if title and href:
            out.append((title, "".join(href)))
    return out


def _filter(
    site: Site,
    candidates: list[tuple[str, str]],
    query: str,
) -> list[Result]:
    """Drop candidates that fail URL/keyword patterns or score threshold.

    Filter order (cheap → expensive):
        1. excluded_paths      — reject if URL contains any excluded path
        2. excluded_keywords   — reject if title contains any excluded keyword
        3. product_pattern     — reject if URL doesn't look like a product
        4. min_score           — reject if relevance score is too low
    """
    out: list[Result] = []
    for title, url in candidates:
        if any(p in url for p in site.excluded_paths):
            continue
        if any(k in title.lower() for k in site.excluded_keywords):
            continue
        if not site.product_pattern.match(url):
            continue
        s = score(query, title)
        if s < site.min_score:
            continue
        out.append(Result(score=s, title=title, url=url))
    return out


async def search(query: str) -> dict[str, list[Result]]:
    """Search all sites, return {site_name: [Result, ...]}."""
    out: dict[str, list[Result]] = {}

    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:

        # soft98 — Playwright (form POST)
        try:
            candidates = await _fetch_soft98(query)
            out["soft98"] = _filter(SITES["soft98"], candidates, query)
        except Exception:
            out["soft98"] = []

        # yasdl — httpx GET
        try:
            candidates = await _fetch_yasdl(client, query)
            out["yasdl"] = _filter(SITES["yasdl"], candidates, query)
        except Exception:
            out["yasdl"] = []


        # p30download — httpx GET
        try:
            candidates = await _fetch_p30download(client, query)
            out["p30download"] = _filter(SITES["p30download"], candidates, query)
        except Exception:
            out["p30download"] = []

        

    return out

