"""Lädt Emote-Namen von BetterTTV (BTTV) und 7TV und parst Twitch-Emote-Tags.

- Twitch-eigene Emotes liefert Twitch selbst pro Nachricht im IRC-Tag `emotes`
  mit (Positions-Angaben) -> keine externe Liste nötig.
- BTTV und 7TV sind reiner Text. Deren Emote-Namen werden per öffentlicher API
  geladen (global + channel-spezifisch) und im Chat-Text abgeglichen.
"""

from typing import Set

import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def _get_json(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


async def fetch_channel_emote_names(room_id: str) -> Set[str]:
    """Sammelt alle BTTV- und 7TV-Emote-Namen (global + für diesen Channel)."""
    names: Set[str] = set()
    async with aiohttp.ClientSession() as session:
        # --- BetterTTV: global ---
        data = await _get_json(session, "https://api.betterttv.net/3/cached/emotes/global")
        if isinstance(data, list):
            for e in data:
                code = e.get("code")
                if code:
                    names.add(code)

        # --- BetterTTV: channel-spezifisch (inkl. geteilter Emotes) ---
        if room_id:
            data = await _get_json(
                session, f"https://api.betterttv.net/3/cached/users/twitch/{room_id}"
            )
            if isinstance(data, dict):
                for key in ("channelEmotes", "sharedEmotes"):
                    for e in data.get(key) or []:
                        code = e.get("code")
                        if code:
                            names.add(code)

        # --- 7TV: global ---
        data = await _get_json(session, "https://7tv.io/v3/emote-sets/global")
        if isinstance(data, dict):
            for e in data.get("emotes") or []:
                name = e.get("name")
                if name:
                    names.add(name)

        # --- 7TV: channel-spezifisch ---
        if room_id:
            data = await _get_json(session, f"https://7tv.io/v3/users/twitch/{room_id}")
            if isinstance(data, dict):
                emote_set = data.get("emote_set") or {}
                for e in emote_set.get("emotes") or []:
                    name = e.get("name")
                    if name:
                        names.add(name)

    return names


def twitch_emote_names(text: str, emotes_tag) -> Set[str]:
    """Extrahiert die Twitch-Emote-Texte aus dem IRC-`emotes`-Tag einer Nachricht.

    Tag-Format: "emoteID:start-end,start-end/emoteID2:start-end"
    Positionen sind inklusive Zeichen-Indizes in der Nachricht.
    """
    names: Set[str] = set()
    if not emotes_tag:
        return names
    for part in str(emotes_tag).split("/"):
        if ":" not in part:
            continue
        _id, positions = part.split(":", 1)
        first = positions.split(",")[0]
        if "-" not in first:
            continue
        a, b = first.split("-", 1)
        try:
            a, b = int(a), int(b)
        except ValueError:
            continue
        if 0 <= a <= b < len(text):
            names.add(text[a : b + 1])
    return names
