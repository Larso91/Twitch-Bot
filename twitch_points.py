"""Twitch Channel Points (Helix + EventSub-WebSocket) fuer die Verlosung.

Anders als der App-Token in twitch_api.py braucht die Punkte-Verwaltung einen
NUTZER-Token des Broadcasters (Channel-Inhabers) mit dem Scope
``channel:manage:redemptions``. Diesen Token holt man einmalig per
``get_twitch_token.py`` (Refresh Token). Twitch erlaubt es bewusst NICHT, den
Punktestand eines Zuschauers auszulesen oder Punkte direkt abzuziehen – der
einzige offizielle Weg, Punkte auszugeben, ist eine *Custom Reward*:

    1. Belohnung mit Kosten anlegen  -> create_reward()
    2. Zuschauer loest sie ein       -> Twitch zieht die Punkte automatisch ab
    3. Bot bekommt das Event live    -> EventSub-WebSocket -> on_redemption()
    4. Spaeter: Einloesungen erstatten (CANCELED) oder bestaetigen (FULFILLED)

Die Einloesungen landen in der Twitch-"Warteschlange" (UNFULFILLED), solange
``should_redemptions_skip_request_queue=False`` ist – nur dann lassen sie sich
spaeter wieder erstatten.
"""

import asyncio
import json
import os
import time
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_HELIX = "https://api.twitch.tv/helix"
_EVENTSUB_WS = "wss://eventsub.wss.twitch.tv/ws"
_REDEMPTION_ADD = "channel.channel_points_custom_reward_redemption.add"
_REFRESH_FILE = "twitch_refresh_token.txt"


class TwitchPoints:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 broadcaster_login: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.broadcaster_login = broadcaster_login.lower()
        self._token: Optional[str] = None
        self._token_expires = 0.0
        self.broadcaster_id: Optional[str] = None
        # Callback fuer eingehende Einloesungen (vom EventSub-Loop aufgerufen).
        self.on_redemption: Optional[Callable[[Dict], Awaitable[None]]] = None

    @classmethod
    def from_env(cls) -> Optional["TwitchPoints"]:
        cid = os.environ.get("TWITCH_CLIENT_ID", "").strip()
        secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
        refresh = os.environ.get("TWITCH_BC_REFRESH_TOKEN", "").strip()
        if not refresh:
            try:
                with open(_REFRESH_FILE, encoding="utf-8") as f:
                    refresh = f.read().strip()
            except OSError:
                pass
        login = os.environ.get("TWITCH_CHANNEL", "").strip().lower()
        if cid and secret and refresh and login:
            return cls(cid, secret, refresh, login)
        return None

    # ------------------------------------------------------------------ #
    # Token / HTTP
    # ------------------------------------------------------------------ #

    async def _get_token(self, force: bool = False) -> Optional[str]:
        if not force and self._token and time.time() < self._token_expires - 60:
            return self._token
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    _TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status != 200:
                        body = (await r.text())[:200]
                        print(f"Twitch-Punkte: Token-Refresh fehlgeschlagen HTTP {r.status}: {body}")
                        return None
                    j = await r.json()
                    self._token = j.get("access_token")
                    self._token_expires = time.time() + int(j.get("expires_in", 0))
                    # Twitch rotiert bei vertraulichen Clients gelegentlich den
                    # Refresh Token – neuen sichern, damit der naechste Start klappt.
                    new_refresh = j.get("refresh_token")
                    if new_refresh and new_refresh != self.refresh_token:
                        self.refresh_token = new_refresh
                        try:
                            with open(_REFRESH_FILE, "w", encoding="utf-8") as f:
                                f.write(new_refresh)
                        except OSError:
                            pass
                    return self._token
        except Exception as e:
            print(f"Twitch-Punkte: Token-Fehler: {e}")
            return None

    async def _request(self, method: str, path: str, *, params=None, json_body=None):
        """Helix-Request mit einem Retry bei 401 (Token erneuern).

        Rueckgabe: (status, parsed_json_or_None). status<0 bei Netzwerkfehler.
        """
        url = f"{_HELIX}{path}"
        for attempt in range(2):
            token = await self._get_token(force=attempt == 1)
            if not token:
                return -1, None
            headers = {
                "Client-Id": self.client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.request(
                        method, url, headers=headers, params=params, json=json_body,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status == 401 and attempt == 0:
                            continue
                        body = None
                        if r.content_type == "application/json":
                            try:
                                body = await r.json()
                            except Exception:
                                body = None
                        return r.status, body
            except Exception as e:
                print(f"Twitch-Punkte: HTTP-Fehler {method} {path}: {e}")
                return -1, None
        return -1, None

    async def ensure_ready(self) -> bool:
        """Stellt sicher, dass Token + Broadcaster-ID vorhanden sind."""
        if self.broadcaster_id:
            return True
        status, body = await self._request("GET", "/users")
        if status == 200 and body and body.get("data"):
            self.broadcaster_id = body["data"][0]["id"]
            return True
        print(f"Twitch-Punkte: Broadcaster-ID konnte nicht geladen werden (HTTP {status}).")
        return False

    # ------------------------------------------------------------------ #
    # Custom Rewards
    # ------------------------------------------------------------------ #

    async def cleanup_rewards_by_title(self, title: str) -> None:
        """Loescht eigene (vom Bot verwaltbare) Belohnungen mit diesem Titel.

        Verhindert "duplicate reward title"-Fehler, falls eine vorige Verlosung
        abgestuerzt ist und die Belohnung nie entfernt wurde.
        """
        status, body = await self._request(
            "GET", "/channel_points/custom_rewards",
            params={"broadcaster_id": self.broadcaster_id,
                    "only_manageable_rewards": "true"},
        )
        if status != 200 or not body:
            return
        for rw in body.get("data", []):
            if rw.get("title") == title:
                await self.delete_reward(rw["id"])

    async def create_reward(self, title: str, cost: int, prompt: str = "") -> Optional[str]:
        """Legt die Raffle-Belohnung an. Gibt die reward_id zurueck (oder None)."""
        if not await self.ensure_ready():
            return None
        await self.cleanup_rewards_by_title(title)
        status, body = await self._request(
            "POST", "/channel_points/custom_rewards",
            params={"broadcaster_id": self.broadcaster_id},
            json_body={
                "title": title,
                "cost": cost,
                "prompt": prompt or "Kaufe ein Los fuer die Verlosung! Mehrere Lose moeglich.",
                "is_enabled": True,
                "is_user_input_required": False,
                # WICHTIG: in der Warteschlange behalten -> spaeter erstattbar.
                "should_redemptions_skip_request_queue": False,
                "background_color": "#9147FF",
            },
        )
        if status == 200 and body and body.get("data"):
            return body["data"][0]["id"]
        print(f"Twitch-Punkte: Belohnung anlegen fehlgeschlagen (HTTP {status}): {body}")
        return None

    async def set_reward_paused(self, reward_id: str, paused: bool) -> bool:
        status, _ = await self._request(
            "PATCH", "/channel_points/custom_rewards",
            params={"broadcaster_id": self.broadcaster_id, "id": reward_id},
            json_body={"is_paused": paused},
        )
        return status == 200

    async def delete_reward(self, reward_id: str) -> bool:
        status, _ = await self._request(
            "DELETE", "/channel_points/custom_rewards",
            params={"broadcaster_id": self.broadcaster_id, "id": reward_id},
        )
        return status in (200, 204)

    async def list_redemptions(self, reward_id: str, status_filter: str = "UNFULFILLED") -> List[Dict]:
        """Alle Einloesungen einer Belohnung holen (paginiert). Fuer Reconcile."""
        out: List[Dict] = []
        cursor = None
        while True:
            params = {
                "broadcaster_id": self.broadcaster_id,
                "reward_id": reward_id,
                "status": status_filter,
                "first": "50",
            }
            if cursor:
                params["after"] = cursor
            status, body = await self._request(
                "GET", "/channel_points/custom_reward_redemptions", params=params
            )
            if status != 200 or not body:
                break
            out.extend(body.get("data", []))
            cursor = (body.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out

    async def update_redemptions(self, reward_id: str, redemption_ids: List[str],
                                 status: str) -> int:
        """Einloesungen auf FULFILLED (Punkte bleiben weg) oder CANCELED
        (Punkte werden erstattet) setzen. Gibt die Anzahl aktualisierter zurueck.
        """
        updated = 0
        # Helix erlaubt bis zu 50 IDs pro Aufruf.
        for i in range(0, len(redemption_ids), 50):
            chunk = redemption_ids[i:i + 50]
            params = [("broadcaster_id", self.broadcaster_id), ("reward_id", reward_id)]
            params += [("id", rid) for rid in chunk]
            st, body = await self._request(
                "PATCH", "/channel_points/custom_reward_redemptions",
                params=params, json_body={"status": status},
            )
            if st == 200 and body:
                updated += len(body.get("data", []))
            else:
                print(f"Twitch-Punkte: Einloesungen-Update HTTP {st}: {body}")
        return updated

    # ------------------------------------------------------------------ #
    # EventSub (WebSocket)
    # ------------------------------------------------------------------ #

    async def run_eventsub(self):
        """Dauerschleife: verbindet sich mit EventSub und liefert Einloesungen
        an self.on_redemption. Reconnect mit kurzer Pause bei Abbruch.
        """
        if not await self.ensure_ready():
            print("Twitch-Punkte: EventSub nicht gestartet (keine Broadcaster-ID).")
            return
        while True:
            try:
                await self._eventsub_once(_EVENTSUB_WS)
            except Exception as e:
                print(f"Twitch-Punkte: EventSub-Fehler: {e}")
            await asyncio.sleep(5)

    async def _eventsub_once(self, url: str):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url, heartbeat=None,
                                    timeout=aiohttp.ClientTimeout(total=15)) as ws:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    data = json.loads(msg.data)
                    meta = data.get("metadata", {})
                    mtype = meta.get("message_type")
                    if mtype == "session_welcome":
                        session_id = data["payload"]["session"]["id"]
                        ok = await self._subscribe_redemptions(session_id)
                        if ok:
                            print("Twitch-Punkte: EventSub verbunden (Einloesungen werden empfangen).")
                        else:
                            print("Twitch-Punkte: EventSub-Subscription fehlgeschlagen.")
                            break
                    elif mtype == "notification":
                        if meta.get("subscription_type") == _REDEMPTION_ADD:
                            event = data.get("payload", {}).get("event", {})
                            if self.on_redemption:
                                await self.on_redemption(event)
                    elif mtype == "session_reconnect":
                        # Neue URL: aktuelle Verbindung schliessen, aussen neu
                        # verbinden (vereinfacht: Default-URL reicht, neu subscriben).
                        break
                    # session_keepalive / revocation: ignorieren

    async def _subscribe_redemptions(self, session_id: str) -> bool:
        """Abo fuer ALLE Channel-Point-Einloesungen des Broadcasters.

        Ohne reward_id-Filter, damit das Abo nicht bei jeder Verlosung neu
        gesetzt werden muss – die Filterung auf die Raffle-Belohnung passiert
        im Callback (bot.py).
        """
        status, body = await self._request(
            "POST", "/eventsub/subscriptions",
            json_body={
                "type": _REDEMPTION_ADD,
                "version": "1",
                "condition": {"broadcaster_user_id": self.broadcaster_id},
                "transport": {"method": "websocket", "session_id": session_id},
            },
        )
        if status in (200, 202):
            return True
        print(f"Twitch-Punkte: Subscribe HTTP {status}: {body}")
        return False
