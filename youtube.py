import os
import re
from typing import Optional, Dict, Set

import aiohttp

_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:[^&]*&)*v=([\w-]+)",
    r"(?:https?://)?youtu\.be/([\w-]+)",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]+)",
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([\w-]+)",
]

# Bekannte Troll-Video-IDs (werden immer geblockt)
_BLOCKED_IDS: Set[str] = {
    "dQw4w9WgXcQ",  # Rick Astley - Never Gonna Give You Up
    "XfR9iY5y94s",  # Rick Astley - Never Gonna Give You Up (Re-Upload)
    "2Z4m4lnjxkY",  # Trololo / Eduard Khil
    "ub82Xb1C8os",  # Trololol (populäre Version)
}

# Keywords die automatisch geblockt werden (Titel, case-insensitive)
_BLOCKED_KEYWORDS = [
    "rick astley",
    "never gonna give you up",
    "rickroll",
    "rick roll",
    "trololo",
    "trololol",
    "you've been rickrolled",
]


def _load_custom_keywords() -> list:
    raw = os.environ.get("BLOCKED_KEYWORDS", "")
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def is_valid_youtube_url(url: str) -> bool:
    return any(re.search(p, url) for p in _PATTERNS)


def extract_video_id(url: str) -> Optional[str]:
    for p in _PATTERNS:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def is_blocked(video_id: Optional[str], title: str) -> bool:
    if video_id and video_id in _BLOCKED_IDS:
        return True
    title_lower = title.lower()
    all_keywords = _BLOCKED_KEYWORDS + _load_custom_keywords()
    return any(kw in title_lower for kw in all_keywords)


async def get_video_info(url: str) -> Optional[Dict]:
    """Title + Author via YouTube oEmbed — kein API-Key nötig."""
    oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(oembed, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return {
                    "title": data.get("title", "Unbekannter Titel"),
                    "author": data.get("author_name", ""),
                }
    except Exception:
        return None


def _parse_iso_duration(duration: str) -> int:
    """PT1H5M30S -> 3930 Sekunden"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mins * 60 + s


async def get_video_duration(video_id: str, api_key: str) -> Optional[int]:
    """Videolänge in Sekunden via YouTube Data API v3. None = Fehler."""
    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?id={video_id}&part=contentDetails&key={api_key}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return None
                iso = items[0]["contentDetails"]["duration"]
                return _parse_iso_duration(iso)
    except Exception:
        return None
