"""Scorer — pick the best group of download links for a query."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.extractor import DownloadLink


# --------------------------------------------------------------------------- #
# System info (from client)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SystemInfo:
    """Client machine description used to filter downloads."""
    os: str = "windows"
    arch: str = "x64"
    os_version: str = "11"


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_PART_RE = re.compile(r"(?:^|[._-])part[._-]?(\d+)", re.IGNORECASE)

# کلماتی که یعنی این فایل محصول اصلی نیست
_REJECT_KEYWORDS = (
    "filters",
    "cleaner",
    "ccmaker",
    "plugin",
    "addon",
)

# الگوهای platform — regex روی filename/text/section
_PLATFORM_PATTERNS = {
    "windows": re.compile(
        r"ویندوز|windows|(?<![a-z])win(?:32|64|dows)?(?![a-z])",
        re.IGNORECASE,
    ),
    "mac": re.compile(
        r"مکینتاش|macos|osx|darwin|(?<![a-z])mac(?![a-z])",
        re.IGNORECASE,
    ),
    "linux": re.compile(
        r"لینوکس|linux|ubuntu",
        re.IGNORECASE,
    ),
    "android": re.compile(
        r"اندروید|android",
        re.IGNORECASE,
    ),
}

# پلتفرم‌هایی که باید رد شوند
_REJECTED_PLATFORMS = ("mac", "linux", "android")

# کلمات معماری
_ARCH_KEYWORDS = {
    "x64": ("x64", "64-bit", "64bit", "amd64", "win64"),
    "x86": ("x86", "32-bit", "32bit", "i386", "win32"),
    "arm64": ("arm64", "aarch64"),
}

# کلمات پرتابل
_PORTABLE_KEYWORDS = ("portable", "پرتابل", "بدون نصب", "قابل حمل")


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Classified:
    """A DownloadLink with derived metadata used for scoring."""
    link: DownloadLink
    year: int | None
    part: int | None
    platform: str
    arch: str
    is_portable: bool
    is_rejected: bool


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Return True if any keyword appears in text."""
    return any(kw in text for kw in keywords)


def _classify(link: DownloadLink) -> Classified:
    """Extract scoring-relevant metadata from a DownloadLink.

    Combines filename, text, and section into one lowercase haystack
    because each source may carry different pieces of information.
    """
    haystack = " ".join((
        link.filename,
        link.text,
        link.section,
    )).lower()

    year_m = _YEAR_RE.search(haystack)
    year = int(year_m.group(1)) if year_m else None

    part_m = _PART_RE.search(link.filename)
    part = int(part_m.group(1)) if part_m else None

    platform = "unknown"
    for name, pat in _PLATFORM_PATTERNS.items():
        if pat.search(haystack):
            platform = name
            break

    arch = "unknown"
    for name, kws in _ARCH_KEYWORDS.items():
        if _has_any(haystack, kws):
            arch = name
            break

    is_portable = _has_any(haystack, _PORTABLE_KEYWORDS)

    is_rejected = (
        _has_any(haystack, _REJECT_KEYWORDS)
        or platform in _REJECTED_PLATFORMS
    )

    return Classified(
        link=link,
        year=year,
        part=part,
        platform=platform,
        arch=arch,
        is_portable=is_portable,
        is_rejected=is_rejected,
    )


def _arch_ok(link_arch: str, user_arch: str) -> bool:
    """Reject only explicit mismatches; unknown is allowed."""
    if link_arch == "unknown":
        return True
    return link_arch == user_arch


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #

def _group_prefix(filename: str) -> str:
    """Return the part of filename before the .partN marker.

    ``Adobe.Photoshop.2026.v27.10.0.26.part1.rar`` →
    ``Adobe.Photoshop.2026.v27.10.0.26``

    For filenames without a .partN marker, returns the filename itself.
    """
    m = _PART_RE.search(filename)
    if not m:
        return filename
    return filename[: m.start()].rstrip("._-")


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def _query_match(query: str, filename: str) -> float:
    """Fraction of query tokens (space-separated) present in filename."""
    tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return 0.0
    hay = filename.lower()
    hit = sum(1 for t in tokens if t in hay)
    return hit / len(tokens)


def _score_group(query: str, group: list[Classified]) -> float:
    """Score a group of links. Higher is better. First link is representative."""
    first = group[0]
    score = 0.0

    # ۱. تطابق با query — مهم‌ترین سیگنال
    score += 100.0 * _query_match(query, first.link.filename)

    # ۲. اگر فایل محصول اصلی نیست، جریمهٔ سنگین
    if first.is_rejected:
        score -= 500.0

    # ۳. سال نسخه — جدیدتر بهتر
    if first.year:
        score += float(first.year - 2000)

    # ۴. پلتفرم
    if first.platform == "windows":
        score += 50.0
    elif first.platform == "linux":
        score -= 100.0
    elif first.platform == "mac":
        score -= 200.0
    elif first.platform == "android":
        score -= 300.0

    # ۵. پرتابل نامطلوب
    if first.is_portable:
        score -= 300.0

    return score


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def rank(
    query: str,
    links: list[DownloadLink],
    system: SystemInfo | None = None,
) -> list[DownloadLink]:
    """Select and return the best group of download links.

    Multi-part files (``.part1.rar``, ``.part2.rar``, ...) are grouped
    by their common prefix and returned together, ordered by part number.

    Args:
        query: original search query.
        links: list of DownloadLink from extractor.
        system: client machine info. Defaults to windows/x64/11.

    Returns:
        The winning group's links, or empty list if none pass.
    """
    if system is None:
        system = SystemInfo()

    if not links:
        return []

    classified = [_classify(link) for link in links]

    # فقط لینک‌هایی که برای معماری سیستم کاربر مناسبند
    classified = [c for c in classified if _arch_ok(c.arch, system.arch)]

    if not classified:
        return []

    groups: dict[str, list[Classified]] = {}
    for c in classified:
        key = _group_prefix(c.link.filename)
        groups.setdefault(key, []).append(c)

    # گروه‌هایی که لینک اولشان rejected است را کلاً حذف کن
    scored: list[tuple[str, list[Classified], float]] = [
        (key, group, _score_group(query, group))
        for key, group in groups.items()
        if not group[0].is_rejected
    ]

    if not scored:
        return []

    # score desc; on tie, larger group wins (main product is usually multi-part)
    scored.sort(key=lambda item: (-item[2], -len(item[1])))

    best_key, best_group, _best_score = scored[0]

    # اگر حتی نیمی از کلمات query در نام فایل نبود، پاسخ قابل‌اعتماد نیست
    if _query_match(query, best_key) < 0.5:
        return []

    # مرتب‌سازی پارت‌ها به ترتیب شماره
    best_group.sort(key=lambda c: c.part if c.part is not None else 0)

    return [c.link for c in best_group]


# --------------------------------------------------------------------------- #
# Manual test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import asyncio

    from app.core.extractor import extract

    URLS = {
        "soft98": (
            "https://soft98.ir/software/pic-tools/3153-"
            "%D8%AF%D8%A7%D9%86%D9%84%D9%80%D9%88%D8%AF-"
            "%D9%86%D8%B1%D9%85-%D8%A7%D9%81%D9%80%D9%80%D8%B2%D8%A7%D8%B1-"
            "%D9%81%D9%80%D8%AA%D9%80%D9%88%D8%B4%D9%80%D8%A7%D9%BE.html"
        ),
        "yasdl": (
            "https://www.yasdl.com/181146/"
            "%d8%af%d8%a7%d9%86%d9%84%d9%88%d8%af-"
            "adobe-photoshop-elements.html"
        ),
        "p30download": (
            "https://p30download.ir/fa/entry/94000/"
            "adobe-photoshop-elements-2027"
        ),
    }

    async def main() -> None:
        system = SystemInfo(arch="x64", os_version="11")
        for site, url in URLS.items():
            links = await extract(url, site)
            best = rank("photoshop", links, system)
            print(f"\n=== {site} ===")
            print(f"  extracted: {len(links)}  →  selected: {len(best)}")
            for link in best:
                print(f"  - {link.filename}")

    asyncio.run(main())