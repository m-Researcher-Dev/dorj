"""Orchestrator — chain searcher → extractor → scorer."""

from __future__ import annotations

import asyncio

from app.core.extractor import DownloadLink, extract
from app.core.scorer import SystemInfo, rank
from app.core.searcher import search


_SITE_PRIORITY = ("soft98", "p30download", "yasdl")


async def _try_site(
    query: str,
    urls: list[str],
    site: str,
    system: SystemInfo,
) -> list[DownloadLink]:
    """Try URLs of one site in order until first success."""
    for url in urls:
        try:
            links = await extract(url, site)
            best = rank(query, links, system)
            if best:
                return best
        except Exception:
            continue
    return []


async def _try_all_sites(
    query: str,
    grouped: dict[str, list[str]],
    system: SystemInfo,
) -> list[DownloadLink]:
    """Try sites in priority order until first success."""
    for site in _SITE_PRIORITY:
        urls = grouped.get(site, [])
        if not urls:
            continue
        best = await _try_site(query, urls, site, system)
        if best:
            return best
    return []


async def find(
    query: str,
    system: SystemInfo | None = None,
) -> list[DownloadLink]:
    """Search → extract → rank. Return best download link group.

    Searcher returns Result objects; we strip them to URLs here so
    later layers don't know about Result.
    """
    if system is None:
        system = SystemInfo()

    grouped = await search(query)
    urls_by_site = {
        site: [r.url for r in results]
        for site, results in grouped.items()
    }
    return await _try_all_sites(query, urls_by_site, system)


if __name__ == "__main__":
    async def main() -> None:
        user_system = SystemInfo(arch="x86")
        results = await find("photoshop", system=user_system)
        print(f"{len(results)} link(s):")
        for link in results:
            print(f"  - {link.filename}")

    asyncio.run(main())