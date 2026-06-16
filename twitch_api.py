import os
import time
from typing import Optional, Dict

import aiohttp

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_CLIPS_URL = "https://api.twitch.tv/helix/clips"


class TwitchAPI:
    """Schlanker Helix-Client für Clip-Metadaten.

    Nutzt den App-Access-Token (Client-Credentials-Flow) – kein Nutzer-Login
    nötig. Der Token wird gecacht und kurz vor Ablauf erneuert.
    """

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_expires = 0.0

    @classmethod
    def from_env(cls) -> Optional["TwitchAPI"]:
        cid = os.environ.get("TWITCH_CLIENT_ID", "").strip()
        secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
        if cid and secret:
            return cls(cid, secret)
        return None

    async def _get_token(self, force: bool = False) -> Optional[str]:
        if not force and self._token and time.time() < self._token_expires - 60:
            return self._token
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    _TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status != 200:
                        print(f"Twitch-Token fehlgeschlagen: HTTP {r.status}")
                        return None
                    j = await r.json()
                    self._token = j.get("access_token")
                    self._token_expires = time.time() + int(j.get("expires_in", 0))
                    return self._token
        except Exception as e:
            print(f"Twitch-Token Fehler: {e}")
            return None

    async def get_clip(self, slug: str) -> Optional[Dict]:
        """Clip-Metadaten per Helix. None = nicht gefunden/Fehler.

        Rückgabe: {id, title, broadcaster, creator, duration (Sekunden), url}
        """
        for attempt in range(2):
            token = await self._get_token(force=attempt == 1)
            if not token:
                return None
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {token}",
            }
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        _CLIPS_URL,
                        headers=headers,
                        params={"id": slug},
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as r:
                        if r.status == 401 and attempt == 0:
                            continue  # Token erneuern und erneut versuchen
                        if r.status != 200:
                            return None
                        j = await r.json()
                        data = j.get("data", [])
                        if not data:
                            return None
                        c = data[0]
                        return {
                            "id": c.get("id"),
                            "title": (c.get("title") or "").strip() or "Clip",
                            "broadcaster": c.get("broadcaster_name"),
                            "creator": c.get("creator_name"),
                            "duration": float(c.get("duration") or 0),
                            "url": c.get("url"),
                        }
            except Exception as e:
                print(f"Twitch get_clip Fehler: {e}")
                return None
        return None
