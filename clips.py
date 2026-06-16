import re
from typing import Optional

# Twitch-Clip-URLs in allen gaengigen Formaten:
#   https://clips.twitch.tv/<Slug>
#   https://clips.twitch.tv/embed?clip=<Slug>
#   https://www.twitch.tv/<kanal>/clip/<Slug>
#   https://m.twitch.tv/<kanal>/clip/<Slug>
#   https://m.twitch.tv/clip/<Slug>
# Slugs bestehen aus Buchstaben, Ziffern, Binde- und Unterstrichen.
_SLUG = r"([A-Za-z0-9_-]+)"
_PATTERNS = [
    re.compile(r"clips\.twitch\.tv/embed\?clip=" + _SLUG, re.I),
    re.compile(r"clips\.twitch\.tv/" + _SLUG, re.I),
    re.compile(r"(?:www\.|m\.)?twitch\.tv/[A-Za-z0-9_]+/clip/" + _SLUG, re.I),
    re.compile(r"(?:www\.|m\.)?twitch\.tv/clip/" + _SLUG, re.I),
]


def extract_clip_slug(url: str) -> Optional[str]:
    """Zieht den Clip-Slug aus einer Twitch-Clip-URL. None, wenn keine erkannt."""
    if not url:
        return None
    # Query-/Fragment-Anhaenge abschneiden, bevor wir matchen (ausser beim
    # embed?clip=-Format, das den Slug erst in der Query traegt).
    candidate = url.strip()
    for pat in _PATTERNS:
        m = pat.search(candidate)
        if m:
            return m.group(1)
    return None


def is_valid_clip_url(url: str) -> bool:
    return extract_clip_slug(url) is not None


def canonical_clip_url(slug: str) -> str:
    return f"https://clips.twitch.tv/{slug}"
