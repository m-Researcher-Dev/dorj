"""Orchestrator — chain searcher → extractor → scorer."""

from __future__ import annotations

import asyncio

from app.core.extractor import DownloadLink, extract
from app.core.scorer import rank
from app.core.searcher import search


_SITE_PRIORITY = ("soft98", "p30download", "yasdl")


async def _try_site(
    query: str,
    urls: list[str],
    site: str,
) -> list[DownloadLink]:
    """Try URLs of one site in order until first success."""
    for url in urls:
        try:
            links = await extract(url, site)
            best = rank(query, links)
            if best:
                return best
        except Exception:
            continue
    return []


async def _try_all_sites(
    query: str,
    grouped: dict[str, list[str]],
) -> list[DownloadLink]:
    """Try sites in priority order until first success."""
    for site in _SITE_PRIORITY:
        urls = grouped.get(site, [])
        if not urls:
            continue
        best = await _try_site(query, urls, site)
        if best:
            return best
    return []


async def find(query: str) -> list[DownloadLink]:
    """Search → extract → rank. Return best download link group.

    Searcher returns Result objects; we strip them to URLs here so
    later layers don't know about Result.
    """
    grouped = await search(query)
    urls_by_site = {
        site: [r.url for r in results]
        for site, results in grouped.items()
    }
    return await _try_all_sites(query, urls_by_site)

if __name__ == "__main__":
    async def main() -> None:
        results = await find("photoshop")
        print(f"{len(results)} link(s):")
        for link in results:
            print(f"  - {link.filename}")
            print(f"  - {link.url}")

    asyncio.run(main())