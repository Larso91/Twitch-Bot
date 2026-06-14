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
_UNSET = object()  # Sentinel: "kein position-Argument uebergeben"


class YouTubePlaylist:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 playlist_id: str, insert_position: Optional[int] = 0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.playlist_id = playlist_id
        # Position, an der neue Songs in die Playlist kommen:
        #   0    = ganz vorne (als Naechstes) [Standard]
        #   None = ans Ende (API-Standardverhalten)
        self.insert_position = insert_position
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    @classmethod
    def from_env(cls) -> Optional["YouTubePlaylist"]:
        """Erzeugt eine Instanz, wenn alle nötigen Env-Variablen gesetzt sind."""
        cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
        secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
        refresh = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
        playlist = os.environ.get("YOUTUBE_PLAYLIST_ID", "").strip()
        pos_raw = os.environ.get("YOUTUBE_INSERT_POSITION", "0").strip().lower()
        if pos_raw in ("", "end", "ende", "none"):
            position = None  # ans Ende
        else:
            try:
                position = max(0, int(pos_raw))
            except ValueError:
                position = 0
        if cid and secret and refresh and playlist:
            return cls(cid, secret, refresh, playlist, insert_position=position)
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
                        txt = await resp.text()
                        print(f"[YT] Token-Fehler {resp.status}: {txt[:200]}")
                        return None
                    j = await resp.json()
                    self._access_token = j.get("access_token")
                    self._expires_at = time.time() + int(j.get("expires_in", 3600))
                    return self._access_token
        except Exception as e:
            print(f"[YT] Token-Ausnahme: {e}")
            return None

    async def add(self, video_id: str, position=_UNSET) -> Optional[str]:
        """Fügt ein Video zur Playlist hinzu. Gibt die playlistItem-ID zurück.

        `position` ueberschreibt die Standard-Einsortierung (z.B. fuer
        'direkt hinter dem laufenden Song'). None = ans Ende.
        """
        token = await self._get_access_token()
        if not token:
            return None
        pos = self.insert_position if position is _UNSET else position
        snippet = {
            "playlistId": self.playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
        if pos is not None:
            snippet["position"] = pos
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_API_BASE}?part=snippet",
                    json={"snippet": snippet},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status in (200, 201):
                        j = await resp.json()
                        return j.get("id")
                    # Position evtl. ungueltig (z.B. groesser als Playlist) ->
                    # einmal ohne Position erneut versuchen (ans Ende).
                    if "position" in snippet:
                        snippet.pop("position")
                        async with session.post(
                            f"{_API_BASE}?part=snippet",
                            json={"snippet": snippet},
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=_TIMEOUT,
                        ) as resp2:
                            if resp2.status in (200, 201):
                                j = await resp2.json()
                                return j.get("id")
                            txt = await resp2.text()
                            print(f"[YT] Insert-Fehler {resp2.status}: {txt[:300]}")
                            return None
                    txt = await resp.text()
                    print(f"[YT] Insert-Fehler {resp.status}: {txt[:300]}")
                    return None
        except Exception as e:
            print(f"[YT] Insert-Ausnahme: {e}")
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

    async def list(self) -> list:
        """Liest die komplette Playlist aus (in Reihenfolge).

        Gibt eine Liste von Dicts zurueck:
            {"item_id": ..., "video_id": ..., "title": ...}
        Nicht abspielbare/geloeschte Eintraege ohne Video-ID werden uebersprungen.
        """
        token = await self._get_access_token()
        if not token:
            return []
        items: list = []
        page: Optional[str] = None
        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    params = {
                        "part": "snippet,contentDetails",
                        "maxResults": "50",
                        "playlistId": self.playlist_id,
                    }
                    if page:
                        params["pageToken"] = page
                    async with session.get(
                        _API_BASE,
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            print(f"[YT] List-Fehler {resp.status}: {txt[:200]}")
                            return items
                        j = await resp.json()
                        for it in j.get("items", []):
                            cd = it.get("contentDetails") or {}
                            sn = it.get("snippet") or {}
                            vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
                            if not vid:
                                continue
                            items.append(
                                {
                                    "item_id": it.get("id"),
                                    "video_id": vid,
                                    "title": sn.get("title", ""),
                                }
                            )
                        page = j.get("nextPageToken")
                        if not page:
                            break
        except Exception as e:
            print(f"[YT] List-Ausnahme: {e}")
        return items
