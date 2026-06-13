"""YouTube-Playlist-Schreibzugriff via OAuth 2.0.

Fügt Songrequests in eine echte YouTube-Playlist ein (playlistItems.insert)
und entfernt sie wieder (playlistItems.delete). Benötigt OAuth-Credentials
(Client ID/Secret + Refresh Token), da Schreibzugriff auf den eigenen Account
erfolgt. Ein reiner API-Key reicht dafür NICHT.

Konfiguration über Environment-Variablen:
    YOUTUBE_CLIENT_ID
    YOUTUBE_CLIENT_SECRET
    YOUTUBE_REFRESH_TOKEN   (einmalig via get_refresh_token.py holen)
    YOUTUBE_PLAYLIST_ID     (steht in der Playlist-URL hinter list=)
"""

import os
import time
from typing import Optional

import aiohttp

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/youtube/v3/playlistItems"
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class YouTubePlaylist:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, playlist_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.playlist_id = playlist_id
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    @classmethod
    def from_env(cls) -> Optional["YouTubePlaylist"]:
        """Erzeugt eine Instanz, wenn alle nötigen Env-Variablen gesetzt sind."""
        cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
        secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
        refresh = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
        playlist = os.environ.get("YOUTUBE_PLAYLIST_ID", "").strip()
        if cid and secret and refresh and playlist:
            return cls(cid, secret, refresh, playlist)
        return None

    async def _get_access_token(self) -> Optional[str]:
        # Access Token cachen bis 60s vor Ablauf.
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_TOKEN_URL, data=data, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    j = await resp.json()
                    self._access_token = j.get("access_token")
                    self._expires_at = time.time() + int(j.get("expires_in", 3600))
                    return self._access_token
        except Exception:
            return None

    async def add(self, video_id: str) -> Optional[str]:
        """Fügt ein Video zur Playlist hinzu. Gibt die playlistItem-ID zurück."""
        token = await self._get_access_token()
        if not token:
            return None
        body = {
            "snippet": {
                "playlistId": self.playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_API_BASE}?part=snippet",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status in (200, 201):
                        j = await resp.json()
                        return j.get("id")
                    return None
        except Exception:
            return None

    async def remove(self, item_id: str) -> bool:
        """Entfernt einen Eintrag (per playlistItem-ID) aus der Playlist."""
        token = await self._get_access_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{_API_BASE}?id={item_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    return resp.status in (200, 204)
        except Exception:
            return False
