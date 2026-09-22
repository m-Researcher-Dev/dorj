"""Extractor — product page URL → download links with metadata."""

from __future__ import annotations

import json
import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BROWSER_TIMEOUT_MS = 30_000

_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DownloadLink:
    """One download link with metadata used by the scorer."""

    url: str
    filename: str
    text: str
    section: str
    site: str


def _extract_filename(url: str) -> str:
    """Return the last path segment of url, without query or fragment."""
    return urlparse(url).path.rsplit("/", 1)[-1]


def _normalize_text(text: str) -> str:
    """Collapse all whitespace runs into single spaces and strip."""
    return _WS_RE.sub(" ", text).strip()


async def _fetch_html(url: str) -> str:
    """Fetch a product page HTML via Playwright (waits for JS/AJAX)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                locale="fa-IR",
            )
            page = await context.new_page()
            page.set_default_timeout(_BROWSER_TIMEOUT_MS)

            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=_BROWSER_TIMEOUT_MS)

            return await page.content()
        finally:
            await browser.close()


def _extract_soft98(soup: BeautifulSoup) -> list[DownloadLink]:
    """Extract download links from soft98 product page."""
    dl = soup.select_one("dl#dbdll")
    if not dl:
        return []

    raw = dl.get("data-json", "")
    if not raw:
        return []

    try:
        data = json.loads("".join(raw))
    except json.JSONDecodeError:
        return []

    out: list[DownloadLink] = []
    for value in data.values():
        if not isinstance(value, dict):
            continue
        scheme = value.get("bddls", "")
        host = value.get("bddlh", "")
        path = value.get("bddlp", "")
        if not (scheme and host and path):
            continue
        url = f"{scheme}://{host}{path}"
        out.append(
            DownloadLink(
                url=url,
                filename=_extract_filename(url),
                text="",
                section="",
                site="soft98",
            )
        )
    return out
def _extract_yasdl(soup: BeautifulSoup) -> list[DownloadLink]:
    """Extract download links from yasdl product page."""
    container = soup.select_one("ul#dl-box1")
    if not container:
        return []

    out: list[DownloadLink] = []
    for a in container.select("a[href]"):
        href = a["href"].strip()
        if not href.startswith(("http://", "https://")):
            continue
        text = _normalize_text(a.get_text(separator=" ", strip=True))
        out.append(
            DownloadLink(
                url=href,
                filename=_extract_filename(href),
                text=text,
                section="",
                site="yasdl",
            )
        )
    return out
def _extract_p30download(soup: BeautifulSoup) -> list[DownloadLink]:
    """Extract download links from p30download product page."""
    container = soup.select_one("div#dlbox")
    if not container:
        return []

    first_strong = container.select_one("strong")
    section = ""
    if first_strong:
        section = _normalize_text(first_strong.get_text(strip=True))

    out: list[DownloadLink] = []
    for a in container.select("a[href]"):
        href = a["href"].strip()
        if not href.startswith(("http://", "https://")):
            continue
        text = _normalize_text(a.get_text(separator=" ", strip=True))
        out.append(
            DownloadLink(
                url=href,
                filename=_extract_filename(href),
                text=text,
                section=section,
                site="p30download",
            )
        )
    return out

async def extract(url: str, site: str) -> list[DownloadLink]:
    """Fetch product page and return its download links with metadata.

    Args:
        url: product page URL (from searcher).
        site: one of "soft98", "yasdl", "p30download".

    Returns:
        List of DownloadLink.

    Raises:
        ValueError: if site is unknown.
    """
    html = await _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    if site == "soft98":
        return _extract_soft98(soup)
    if site == "yasdl":
        return _extract_yasdl(soup)
    if site == "p30download":
        return _extract_p30download(soup)

    raise ValueError(f"unknown site: {site!r}")


