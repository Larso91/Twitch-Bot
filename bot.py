import asyncio
import os
import random
import time

# Lokale Entwicklung: Variablen aus einer .env-Datei laden, falls vorhanden.
# Auf Railway o.ae. werden die Env-Vars von der Plattform gesetzt; dort ist
# weder eine .env-Datei noch python-dotenv noetig (Import per try/except).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from aiohttp import web
from twitchio.ext import commands

from chat import ChatExtras
from clips import canonical_clip_url, extract_clip_slug, is_valid_clip_url
from database import Database
from emotes import fetch_channel_emote_names, twitch_emote_names
from jokes import get_random_joke
from twitch_api import TwitchAPI
from twitch_points import TwitchPoints
from youtube_playlist import YouTubePlaylist
from youtube import (
    extract_video_id,
    get_video_duration,
    get_video_info,
    is_blocked,
    is_valid_youtube_url,
)


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=os.environ["TWITCH_TOKEN"],
            prefix="!",
            initial_channels=[os.environ["TWITCH_CHANNEL"]],
        )
        self.db = Database(os.environ.get("DB_PATH", "queue.db"))
        self.channel_name = os.environ["TWITCH_CHANNEL"].lower()
        self.joke_interval = int(os.environ.get("JOKE_INTERVAL_MINUTES", "30")) * 60
        self.yt_api_key = os.environ.get("YOUTUBE_API_KEY", "")
        self.max_duration = int(os.environ.get("MAX_SONG_DURATION_MINUTES", "5"))
        # Datei, in die die dauerhaft wachsende Streamliste geschrieben wird.
        self.streamlist_file = os.environ.get("STREAMLIST_FILE", "streamlist.txt")
        # Zufallsantworten + Emoji-Combos.
        self.chat = ChatExtras(
            reply_chance=float(os.environ.get("RANDOM_REPLY_CHANCE", "0.15")),
            combo_threshold=int(os.environ.get("COMBO_THRESHOLD", "3")),
        )
        self._emotes_loaded = False
        self._room_id = None
        # YouTube-Playlist-Sync (nur aktiv, wenn OAuth-Daten gesetzt sind).
        self.yt_playlist = YouTubePlaylist.from_env()
        # In-Memory-Abbild der YouTube-Playlist (Wiedergabe-Quelle des Players).
        # Wird beim Start aus YouTube geladen und bei jedem add/remove lokal
        # nachgefuehrt, damit der Player-Poll NICHT staendig die YouTube-API
        # trifft (Quota!). Eintraege: item_id, video_id, title, requester, url.
        self._pl = []
        self._pl_sync_seconds = int(os.environ.get("PLAYLIST_SYNC_SECONDS", "300"))
        # Wiedergabe-Status vom Player gemeldet (fuer das OBS-Overlay/Restzeit).
        self._now = None  # {video_id, position, duration, playing, ts}
        # Skip-Signal: wird bei !skip / Media_Next hochgezaehlt; der Player
        # springt dann zum naechsten Song, OHNE ihn aus der Playlist zu loeschen.
        self._skip_seq = 0
        # Web-Player (OBS-Browser-Quelle).
        self.player_token = os.environ.get("PLAYER_TOKEN", "").strip()
        # Eigener Token fuer die Clip-Endpunkte; faellt auf PLAYER_TOKEN zurueck,
        # falls nicht gesetzt (so kann man Player und Clips getrennt absichern).
        self.clips_token = os.environ.get("CLIPS_TOKEN", "").strip() or self.player_token
        self._web_started = False
        # Clip-Queue: Fallback-Spielzeit pro Clip, falls die echte Laenge nicht
        # bekannt ist (z.B. ohne Twitch-API). Twitch-Clips sind max. 60 s.
        self.clip_play_seconds = int(os.environ.get("CLIP_PLAY_SECONDS", "35"))
        # Twitch Helix API (Client-Credentials) fuer Clip-Metadaten: echter Titel,
        # Laenge und Existenzpruefung. Aktiv, wenn Client-ID + Secret gesetzt sind.
        self.twitch_api = TwitchAPI.from_env()
        # Channel-Points-Verlosung (Helix + EventSub). Aktiv, wenn zusaetzlich ein
        # Broadcaster-Refresh-Token (TWITCH_BC_REFRESH_TOKEN) gesetzt ist.
        self.points = TwitchPoints.from_env()
        # Titel der Raffle-Belohnung (max. 45 Zeichen, im Channel eindeutig).
        self.raffle_title = os.environ.get("RAFFLE_REWARD_TITLE", "🎟️ Verlosung – Los kaufen")
        self.raffle_default_cost = int(os.environ.get("RAFFLE_DEFAULT_COST", "500"))

    async def event_ready(self):
        print(f"Bot gestartet: {self.nick}")
        print(f"Kanal:         {self.channel_name}")
        print(f"Max. Länge:    {self.max_duration} Min.")
        if self.yt_api_key:
            print("Längencheck:   AKTIV (YouTube API Key gefunden)")
        else:
            print("Längencheck:   INAKTIV (kein YOUTUBE_API_KEY gesetzt)")
        print(f"Witze-Interval:{self.joke_interval // 60} Min.")
        if self.twitch_api:
            print("Clip-Queue:    AKTIV (Twitch-API: echter Titel + Laenge) -> /clips")
        else:
            print(f"Clip-Queue:    AKTIV ({self.clip_play_seconds}s/Clip, ohne API) -> /clips")
        print(f"Zufallsantworten: {int(self.chat.reply_chance * 100)}% | Combo ab {self.chat.combo_threshold}")
        if self.yt_playlist:
            print("YT-Playlist:   AKTIV (Songrequests werden synchronisiert)")
        else:
            print("YT-Playlist:   INAKTIV (keine OAuth-Daten gesetzt)")
        if self.points:
            print("Verlosung:     AKTIV (Channel Points -> !raffle)")
        else:
            print("Verlosung:     INAKTIV (kein TWITCH_BC_REFRESH_TOKEN gesetzt)")
        asyncio.create_task(self._joke_loop())
        if not self._web_started:
            self._web_started = True
            asyncio.create_task(self._start_web())
            if self.yt_playlist:
                asyncio.create_task(self._playlist_sync_loop())
            if self.points:
                self.points.on_redemption = self._on_redemption
                asyncio.create_task(self.points.run_eventsub())
                asyncio.create_task(self._raffle_reconcile())

    async def event_message(self, message):
        if message.echo:
            return
        content = message.content or ""
        tags = message.tags or {}

        # BTTV/7TV-Emote-Listen einmalig laden, sobald die Channel-ID bekannt ist.
        if not self._emotes_loaded:
            room_id = tags.get("room-id")
            if room_id:
                self._emotes_loaded = True
                self._room_id = room_id
                asyncio.create_task(self._load_emotes(room_id))

        # Auf Nicht-Befehle reagieren: Emote-Combos & Zufallsantworten.
        if not content.startswith("!") and self.db.get_setting("reactions_enabled", "1") == "1":
            tw_emotes = twitch_emote_names(content, tags.get("emotes"))
            reply = self.chat.process(content, twitch_emotes=tw_emotes)
            if reply and message.channel:
                await message.channel.send(reply)
        await self.handle_commands(message)

    async def _load_emotes(self, room_id):
        try:
            names = await fetch_channel_emote_names(room_id)
            self.chat.set_word_emotes(names)
            print(f"Emotes geladen: {len(names)} BTTV/7TV-Emotes (Channel-ID {room_id})")
        except Exception as e:
            print(f"Emotes konnten nicht geladen werden: {e}")

    async def _joke_loop(self):
        await asyncio.sleep(10)
        while True:
            await asyncio.sleep(self.joke_interval)
            channel = self.get_channel(self.channel_name)
            if channel:
                joke = await get_random_joke()
                await channel.send(f"Witz des Tages: {joke}")

    # ------------------------------------------------------------------ #
    # Song Requests
    # ------------------------------------------------------------------ #

    @commands.command(name="sr", aliases=["songrequest"])
    async def sr(self, ctx: commands.Context, *, url: str = None):
        if self.db.get_setting("sr_enabled", "1") != "1":
            await ctx.send(f"@{ctx.author.name} Song Requests sind gerade deaktiviert.")
            return

        if not url:
            await ctx.send(f"@{ctx.author.name} Benutze: !sr <YouTube-Link>")
            return

        url = url.strip().split()[0]

        if not is_valid_youtube_url(url):
            await ctx.send(
                f"@{ctx.author.name} Bitte nur YouTube-Links! (youtube.com oder youtu.be)"
            )
            return

        info = await get_video_info(url)
        if not info:
            await ctx.send(f"@{ctx.author.name} Video nicht gefunden oder nicht verfügbar.")
            return

        video_id = extract_video_id(url)

        # Troll-Filter
        if is_blocked(video_id, info["title"]):
            await ctx.send(
                f"@{ctx.author.name} Dieser Song ist auf der Blockliste. Bitte einen anderen wählen!"
            )
            return

        # Längenbegrenzung (nur wenn API-Key vorhanden)
        if self.yt_api_key and video_id:
            duration = await get_video_duration(video_id, self.yt_api_key)
            if duration is None:
                await ctx.send(
                    f"@{ctx.author.name} Videolänge konnte nicht geprüft werden. Bitte erneut versuchen."
                )
                return
            if duration > self.max_duration * 60:
                m, s = divmod(duration, 60)
                await ctx.send(
                    f"@{ctx.author.name} Song zu lang! ({m}:{s:02d} Min. — Max: {self.max_duration} Min.)"
                )
                return

        song_id, position = self.db.add_song(url, info["title"], ctx.author.name)
        self._write_streamlist_file()

        # In die echte YouTube-Playlist eintragen (falls aktiviert) – hinter dem
        # laufenden Song und hinter schon wartenden Requests (FIFO).
        yt_note = ""
        when = "in die Warteschlange aufgenommen"
        if self.yt_playlist and video_id:
            pos = self._insert_position()
            item_id = await self.yt_playlist.add(video_id, position=pos)
            if item_id:
                self.db.set_yt_item_id(song_id, item_id)
                # Sofort ins In-Memory-Abbild, damit der Player es direkt sieht.
                self._pl_insert(pos, item_id, video_id, info["title"], ctx.author.name, url)
                ci = self._current_index()
                ahead = pos - (ci + 1) if ci >= 0 else pos - 1
                when = "spielt als Nächstes" if ahead <= 0 else f"an Position {ahead + 1} der Warteschlange"
            else:
                yt_note = " (Hinweis: konnte nicht zur YouTube-Playlist hinzugefügt werden)"

        await ctx.send(
            f"@{ctx.author.name} {info['title']} — {when}{yt_note}"
        )

    async def _remove_from_yt(self, song):
        """Entfernt einen Song aus der YouTube-Playlist (falls aktiviert)."""
        if self.yt_playlist and song and song.get("yt_item_id"):
            try:
                await self.yt_playlist.remove(song["yt_item_id"])
            except Exception as e:
                print(f"YT-Playlist remove fehlgeschlagen: {e}")

    # ------------------------------------------------------------------ #
    # Playlist als Wiedergabe-Quelle (In-Memory-Abbild der YT-Playlist)
    # ------------------------------------------------------------------ #

    async def _playlist_sync_loop(self):
        """Gleicht das In-Memory-Abbild regelmaessig mit YouTube ab.

        Faengt externe Aenderungen ab (z.B. manuell in YouTube editiert) und
        stellt nach einem Neustart die Liste wieder her. Laeuft selten, um die
        YouTube-API-Quota zu schonen.
        """
        while True:
            try:
                await self._reload_playlist_from_yt()
            except Exception as e:
                print(f"Playlist-Sync Fehler: {e}")
            await asyncio.sleep(self._pl_sync_seconds)

    async def _reload_playlist_from_yt(self):
        """Laedt die echte YouTube-Playlist und reichert sie mit Requester-
        Namen aus der SQLite-Queue an (per yt_item_id).

        Wichtig: YouTube liefert fuer frisch eingefuegte playlistItems oft
        einen LEEREN snippet.title (Backend-Propagation kann Minuten dauern).
        Wuerde dieser leere Titel uebernommen, verschwaende der Songtitel im
        OBS-Overlay/Player, waehrend der Requester (aus SQLite) bestehen bleibt
        -> "Request von @user" ohne Titel. Daher wird ein leerer API-Titel NICHT
        uebernommen, sondern aus bekannten Quellen rekonstruiert: zuerst die
        SQLite-Queue, dann das bisherige In-Memory-Abbild, zuletzt YouTube
        oEmbed (kein API-Key noetig).
        """
        items = await self.yt_playlist.list()
        # Bereits bekannte Titel aus dem aktuellen Abbild (per item_id).
        prev_titles = {x["item_id"]: x["title"] for x in self._pl if x.get("title")}
        merged = []
        for it in items:
            row = self.db.get_by_yt_item_id(it["item_id"])
            url = row["url"] if row else f"https://youtu.be/{it['video_id']}"
            title = (it.get("title") or "").strip()
            if not title and row and (row["title"] or "").strip():
                title = row["title"].strip()
            if not title:
                title = prev_titles.get(it["item_id"], "")
            if not title:  # letzter Ausweg: oEmbed-Lookup
                info = await get_video_info(url)
                if info:
                    title = info["title"]
            merged.append(
                {
                    "item_id": it["item_id"],
                    "video_id": it["video_id"],
                    "title": title,
                    "requester": row["requester"] if row else None,
                    "url": url,
                }
            )
        self._pl = merged
        print(f"Playlist synchronisiert: {len(merged)} Song(s)")

    def _pl_insert(self, pos, item_id, video_id, title, requester, url):
        """Neuen Song lokal an Position `pos` einfuegen (None/zu gross = ans Ende)."""
        entry = {
            "item_id": item_id,
            "video_id": video_id,
            "title": title,
            "requester": requester,
            "url": url,
        }
        if pos is None or pos >= len(self._pl):
            self._pl.append(entry)
        else:
            self._pl.insert(max(0, pos), entry)

    def _current_index(self):
        """Index des aktuell laufenden Songs im In-Memory-Abbild (per Player-Meldung)."""
        now = self._now
        vid = now.get("video_id") if now else None
        if vid:
            for i, x in enumerate(self._pl):
                if x["video_id"] == vid:
                    return i
        return -1

    def _insert_position(self):
        """Position fuer einen neuen Request.

        Hinter dem laufenden Song UND hinter bereits wartenden Requests, damit
        mehrere Requests in der Reihenfolge ihres Eingangs (FIFO) nacheinander
        laufen – statt sich gegenseitig zu ueberholen (das fuehrte zu
        Rueckwaerts-Reihenfolge).
        """
        if not self._pl:
            return 0
        ci = self._current_index()
        pos = ci + 1 if ci >= 0 else 1  # laeuft nichts Bekanntes -> hinter den ersten Song
        # Ueber bereits wartende Requests (haben einen Requester) hinwegspringen.
        while pos < len(self._pl) and self._pl[pos].get("requester"):
            pos += 1
        return pos

    async def _remove_playlist_item(self, item_id):
        """Entfernt einen Eintrag aus YouTube-Playlist, In-Memory-Abbild und SQLite."""
        if self.yt_playlist:
            try:
                await self.yt_playlist.remove(item_id)
            except Exception as e:
                print(f"YT-Playlist remove fehlgeschlagen: {e}")
        self._pl = [x for x in self._pl if x["item_id"] != item_id]
        self.db.remove_by_yt_item_id(item_id)

    def _queue_view(self):
        """Einheitliche Sicht auf die aktuelle Queue (Reihenfolge = Wiedergabe).

        Mit aktiver YT-Playlist: das In-Memory-Abbild. Sonst: die SQLite-Queue
        (Fallback fuer lokale Tests ohne OAuth).
        """
        if self.yt_playlist:
            return [
                {
                    "title": x["title"],
                    "requester": x.get("requester"),
                    "url": x.get("url") or f"https://youtu.be/{x['video_id']}",
                    "video_id": x["video_id"],
                    "item_id": x["item_id"],
                    "sqlite_id": None,
                }
                for x in self._pl
            ]
        return [
            {
                "title": s["title"],
                "requester": s["requester"],
                "url": s["url"],
                "video_id": extract_video_id(s["url"]),
                "item_id": None,
                "sqlite_id": s["id"],
            }
            for s in self.db.get_queue()
        ]

    async def _queue_remove(self, entry):
        """Entfernt einen Queue-Eintrag (egal ob YT-Playlist oder SQLite-Fallback)."""
        if entry.get("item_id"):
            await self._remove_playlist_item(entry["item_id"])
        elif entry.get("sqlite_id"):
            self.db.remove_by_id(entry["sqlite_id"])

    # ------------------------------------------------------------------ #
    # Web-Player (OBS-Browser-Quelle)
    # ------------------------------------------------------------------ #

    async def _start_web(self):
        port = int(os.environ.get("PORT", "8080"))
        app = web.Application()
        app.router.add_get("/", self._h_root)
        app.router.add_get("/player", self._h_player)
        app.router.add_get("/overlay", self._h_overlay)
        app.router.add_get("/clips", self._h_clips_page)
        app.router.add_get("/api/clips", self._h_clips)
        app.router.add_post("/api/clips/finished", self._h_clip_finished)
        app.router.add_get("/api/clips/finished", self._h_clip_finished)
        app.router.add_get("/api/nowplaying", self._h_nowplaying)
        app.router.add_post("/api/nowplaying", self._h_nowplaying_set)
        app.router.add_get("/api/queue", self._h_queue)
        app.router.add_get("/api/playlist", self._h_playlist)
        app.router.add_post("/api/playlist/remove", self._h_playlist_remove)
        app.router.add_get("/api/playlist/remove", self._h_playlist_remove)
        app.router.add_post("/api/finished", self._h_finished)
        app.router.add_post("/api/skip", self._h_skip)
        app.router.add_get("/api/skip", self._h_skip)  # bequem fuer Hotkey-Tools
        app.router.add_get("/healthz", lambda r: web.Response(text="ok"))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        guard = "mit Token-Schutz" if self.player_token else "OHNE Token (PLAYER_TOKEN setzen!)"
        print(f"Web-Player:    laeuft auf Port {port} -> /player ({guard})")

    def _check_token(self, request) -> bool:
        if not self.player_token:
            return True  # kein Token gesetzt -> offen (nur fuer Tests empfohlen)
        return request.query.get("token") == self.player_token

    def _check_clip_token(self, request) -> bool:
        if not self.clips_token:
            return True  # kein Token gesetzt -> offen (nur fuer Tests empfohlen)
        return request.query.get("token") == self.clips_token

    def _song_json(self, song):
        return {
            "id": song["id"],
            "title": song["title"],
            "requester": song["requester"],
            "url": song["url"],
            "video_id": extract_video_id(song["url"]),
        }

    async def _h_root(self, request):
        return web.Response(
            text="Twitch-Bot laeuft. Player unter /player?token=DEIN_TOKEN",
            content_type="text/plain",
        )

    async def _h_player(self, request):
        try:
            with open("player.html", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            return web.Response(text="player.html nicht gefunden", status=500)
        return web.Response(text=html, content_type="text/html")

    async def _h_overlay(self, request):
        """Transparentes 'Now Playing'-Overlay fuer OBS (Browser-Quelle)."""
        try:
            with open("overlay.html", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            return web.Response(text="overlay.html nicht gefunden", status=500)
        return web.Response(text=html, content_type="text/html")

    async def _h_clips_page(self, request):
        """Clip-Overlay fuer OBS (Browser-Quelle) – spielt Clips nacheinander ab."""
        try:
            with open("clips.html", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            return web.Response(text="clips.html nicht gefunden", status=500)
        return web.Response(text=html, content_type="text/html")

    async def _h_clips(self, request):
        """Liefert die Clip-Queue fuer das Overlay (erster Eintrag = laeuft)."""
        if not self._check_clip_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        clips = self.db.get_clips()
        items = [
            {
                "id": c["id"],
                "slug": c["slug"],
                "url": c["url"],
                "requester": c["requester"],
                "title": c["title"],
                "duration": c["duration"] or 0,
            }
            for c in clips
        ]
        return web.json_response(
            {
                "current": items[0] if items else None,
                "queue": items,
                "play_seconds": self.clip_play_seconds,
            }
        )

    async def _h_clip_finished(self, request):
        """Overlay meldet: aktueller Clip fertig/abgelaufen -> aus Queue entfernen."""
        if not self._check_clip_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            cid = int(request.query.get("id", "0"))
        except ValueError:
            cid = 0
        first = self.db.get_first_clip()
        if first and first["id"] == cid:
            self.db.remove_clip_by_id(cid)
            return web.json_response({"ok": True, "removed": cid})
        return web.json_response({"ok": False, "reason": "not-current"})

    async def _h_nowplaying_set(self, request):
        """Player meldet aktuelle Position/Dauer (fuer Restzeit im Overlay)."""
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except Exception:
            data = {}
        self._now = {
            "video_id": data.get("video_id"),
            "title": (data.get("title") or "").strip() or None,
            "requester": (data.get("requester") or "").strip() or None,
            "position": float(data.get("position", 0) or 0),
            "duration": float(data.get("duration", 0) or 0),
            "playing": bool(data.get("playing", True)),
            "ts": time.time(),
        }
        return web.json_response({"ok": True})

    async def _h_nowplaying(self, request):
        """Liefert den aktuellen Song + (falls bekannt) verstrichene Zeit/Dauer.

        Quelle ist primaer die Meldung des Players (self._now) – das ist exakt
        der Song, der WIRKLICH laeuft. So zeigt das OBS-Overlay zuverlaessig den
        richtigen Titel, auch wenn sich die Playlist-Reihenfolge gerade aendert
        (frueher fiel es faelschlich auf _pl[0] zurueck = falscher Song).
        """
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        now = self._now
        cur = None
        if now and now.get("video_id"):
            cur = {
                "video_id": now["video_id"],
                "title": now.get("title"),
                "requester": now.get("requester"),
            }
            # Falls der Player (noch) keine Metadaten mitschickt: im
            # In-Memory-Abbild der Playlist nachschlagen.
            if not cur["title"] and self._pl:
                match = next((x for x in self._pl if x["video_id"] == now["video_id"]), None)
                if match:
                    cur["title"] = match["title"]
                    if not cur["requester"]:
                        cur["requester"] = match.get("requester")
        elif self.yt_playlist and self._pl:
            # Noch keine Player-Meldung -> bestmoegliche Schaetzung.
            ci = self._current_index()
            e = self._pl[ci] if ci >= 0 else self._pl[0]
            cur = {"video_id": e["video_id"], "title": e["title"], "requester": e.get("requester")}

        out = {
            "title": cur["title"] if cur else None,
            "requester": cur.get("requester") if cur else None,
            "video_id": cur["video_id"] if cur else None,
            "has_time": False,
        }
        if cur and now and now.get("video_id") == cur["video_id"] and now.get("duration", 0) > 0:
            elapsed = now["position"]
            if now.get("playing"):
                elapsed += time.time() - now["ts"]
            out.update(
                {
                    "has_time": True,
                    "playing": now.get("playing", True),
                    "duration": now["duration"],
                    "elapsed": max(0.0, min(elapsed, now["duration"])),
                }
            )
        return web.json_response(out)

    async def _h_queue(self, request):
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        songs = self.db.get_queue()
        out = [self._song_json(s) for s in songs]
        return web.json_response({"current": out[0] if out else None, "queue": out})

    async def _h_playlist(self, request):
        """Liefert die Playlist (In-Memory-Abbild) fuer den Direkt-Player.

        Serviert aus dem Arbeitsspeicher, damit der 3s-Poll des Players NICHT
        bei jedem Aufruf die YouTube-API belastet.
        """
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self.yt_playlist:
            return web.json_response({"playlist_id": None, "items": []})
        items = [
            {
                "item_id": x["item_id"],
                "video_id": x["video_id"],
                "title": x["title"],
                "requester": x.get("requester"),
            }
            for x in self._pl
            if x.get("video_id")
        ]
        return web.json_response(
            {
                "playlist_id": self.yt_playlist.playlist_id,
                "items": items,
                "skip_seq": self._skip_seq,
            }
        )

    async def _h_playlist_remove(self, request):
        """Player meldet: aktueller Song fertig/uebersprungen -> aus Playlist entfernen."""
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        item_id = request.query.get("item_id", "").strip()
        if not item_id:
            return web.json_response({"ok": False, "reason": "no-id"})
        await self._remove_playlist_item(item_id)
        return web.json_response({"ok": True, "removed": item_id})

    async def _h_finished(self, request):
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            fid = int(request.query.get("id", "0"))
        except ValueError:
            fid = 0
        first = self.db.get_first()
        if first and first["id"] == fid:
            song = self.db.remove_first()
            await self._remove_from_yt(song)
            return web.json_response({"ok": True, "removed": fid})
        return web.json_response({"ok": False, "reason": "not-current"})

    async def _h_skip(self, request):
        """Globaler Skip (Media_Next via AHK): zum naechsten Song springen.

        Loescht NICHT aus der Playlist – der Song bleibt erhalten und kommt in
        der Rotation wieder. Entfernen geht nur ueber !wrongsong/!remove/!clearqueue.
        """
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        self._skip_seq += 1
        return web.json_response({"ok": True, "skip_seq": self._skip_seq})

    def _write_streamlist_file(self):
        """Schreibt die komplette Streamliste in eine wachsende Textdatei."""
        try:
            songs = self.db.get_streamlist()
            lines = [
                f"Streamliste — {len(songs)} Song(s)",
                "=" * 40,
            ]
            for i, s in enumerate(songs, 1):
                lines.append(f"{i:>4}. {s['title']} (@{s['requester']}) - {s['url']}")
            with open(self.streamlist_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            print(f"Streamliste konnte nicht geschrieben werden: {e}")

    @commands.command(name="wrongsong", aliases=["ws"])
    async def wrongsong(self, ctx: commands.Context):
        name = ctx.author.name.lower()
        view = self._queue_view()
        # Letzten eigenen Song finden (von hinten).
        entry = next(
            (e for e in reversed(view) if (e["requester"] or "").lower() == name), None
        )
        if not entry:
            await ctx.send(f"@{ctx.author.name} Du hast keinen Song in der Queue.")
            return
        await self._queue_remove(entry)
        await ctx.send(f"@{ctx.author.name} Song entfernt: {entry['title']}")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context):
        view = self._queue_view()
        if not view:
            await ctx.send("Die Queue ist leer. Füge mit !sr <YouTube-Link> Songs hinzu!")
            return
        total = len(view)
        entries = " | ".join(
            f"#{i + 1} {e['title']} (@{e['requester'] or '?'})"
            for i, e in enumerate(view[:5])
        )
        suffix = f" ... und {total - 5} weitere" if total > 5 else ""
        await ctx.send(f"Queue ({total} Songs): {entries}{suffix}")

    @commands.command(name="streamlist", aliases=["sl", "playlist"])
    async def streamlist(self, ctx: commands.Context):
        count = self.db.streamlist_count()
        if count == 0:
            await ctx.send("Noch keine Songs in der Streamliste. Füge welche mit !sr hinzu!")
            return
        await ctx.send(
            f"Streamliste: {count} Song(s) bisher requestet — gespeichert in "
            f"{self.streamlist_file}."
        )

    @commands.command(name="currentsong", aliases=["np", "song"])
    async def current_song(self, ctx: commands.Context):
        view = self._queue_view()
        if not view:
            await ctx.send("Aktuell kein Song in der Queue.")
            return
        ci = self._current_index() if self.yt_playlist else 0
        cur = view[ci] if 0 <= ci < len(view) else view[0]
        await ctx.send(
            f"Aktueller Song: {cur['title']} (von @{cur['requester'] or 'unbekannt'}) -> {cur['url']}"
        )

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            await ctx.send(f"@{ctx.author.name} Nur Mods können Songs überspringen.")
            return
        if not self._pl:
            await ctx.send("Die Playlist ist leer.")
            return
        # Nur weiterspringen – der Song bleibt in der Playlist erhalten.
        self._skip_seq += 1
        await ctx.send("Übersprungen (nächster Song).")

    @commands.command(name="remove", aliases=["sr_remove"])
    async def remove(self, ctx: commands.Context, *, position: str = None):
        if not position or not position.strip().isdigit():
            await ctx.send(f"@{ctx.author.name} Benutze: !remove <Position>")
            return
        pos = int(position.strip())
        view = self._queue_view()
        if pos < 1 or pos > len(view):
            await ctx.send(
                f"@{ctx.author.name} Position {pos} existiert nicht (Queue: {len(view)} Songs)."
            )
            return
        entry = view[pos - 1]
        is_mod = ctx.author.is_mod or ctx.author.is_broadcaster
        is_own = (entry["requester"] or "").lower() == ctx.author.name.lower()
        if not is_mod and not is_own:
            await ctx.send(f"@{ctx.author.name} Du kannst nur deine eigenen Songs entfernen.")
            return
        await self._queue_remove(entry)
        await ctx.send(f"@{ctx.author.name} Entfernt: {entry['title']}")

    @commands.command(name="clearqueue", aliases=["clearsr"])
    async def clearqueue(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        view = self._queue_view()
        count = len(view)
        for entry in view:
            await self._queue_remove(entry)
        # SQLite-Queue zusaetzlich leeren (Requester-Sidecar).
        self.db.clear_queue()
        await ctx.send(f"Queue geleert. {count} Song(s) entfernt.")

    # ------------------------------------------------------------------ #
    # Clip Requests (Twitch-Clips)
    # ------------------------------------------------------------------ #

    @commands.command(name="clip", aliases=["cr", "cliprequest"])
    async def clip(self, ctx: commands.Context, *, url: str = None):
        if self.db.get_setting("clip_enabled", "1") != "1":
            await ctx.send(f"@{ctx.author.name} Clip Requests sind gerade deaktiviert.")
            return
        if not url:
            await ctx.send(f"@{ctx.author.name} Benutze: !clip <Twitch-Clip-Link>")
            return
        url = url.strip().split()[0]
        slug = extract_clip_slug(url)
        if not slug:
            await ctx.send(
                f"@{ctx.author.name} Bitte einen gültigen Twitch-Clip-Link! "
                f"(clips.twitch.tv/… oder twitch.tv/<kanal>/clip/…)"
            )
            return
        if self.db.clip_exists(slug):
            await ctx.send(f"@{ctx.author.name} Dieser Clip ist schon in der Queue.")
            return

        # Mit Twitch-API: echten Titel/Länge holen und Existenz prüfen.
        title, duration = None, 0
        clip_url = canonical_clip_url(slug)
        if self.twitch_api:
            info = await self.twitch_api.get_clip(slug)
            if not info:
                await ctx.send(
                    f"@{ctx.author.name} Clip nicht gefunden oder nicht verfügbar."
                )
                return
            title = info["title"]
            duration = info["duration"]
            clip_url = info.get("url") or clip_url

        _, position = self.db.add_clip(
            slug, clip_url, ctx.author.name, title=title, duration=duration
        )
        what = f"Clip „{title}“" if title else "Clip"
        when = "spielt als Nächstes" if position == 1 else f"an Position {position} der Clip-Queue"
        await ctx.send(f"@{ctx.author.name} {what} {when}.")

    @commands.command(name="wrongclip", aliases=["wc"])
    async def wrongclip(self, ctx: commands.Context):
        removed = self.db.remove_last_clip_by_requester(ctx.author.name)
        if not removed:
            await ctx.send(f"@{ctx.author.name} Du hast keinen Clip in der Queue.")
            return
        await ctx.send(f"@{ctx.author.name} Dein letzter Clip wurde entfernt.")

    @commands.command(name="clipqueue", aliases=["cq", "clips"])
    async def clip_queue(self, ctx: commands.Context):
        clips = self.db.get_clips()
        if not clips:
            await ctx.send("Die Clip-Queue ist leer. Füge mit !clip <Link> Clips hinzu!")
            return
        total = len(clips)
        entries = " | ".join(
            f"#{i + 1} {c['title'] or 'Clip'} (@{c['requester']})"
            for i, c in enumerate(clips[:5])
        )
        suffix = f" ... und {total - 5} weitere" if total > 5 else ""
        await ctx.send(f"Clip-Queue ({total} Clips): {entries}{suffix}")

    @commands.command(name="skipclip")
    async def skip_clip(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            await ctx.send(f"@{ctx.author.name} Nur Mods können Clips überspringen.")
            return
        first = self.db.get_first_clip()
        if not first:
            await ctx.send("Die Clip-Queue ist leer.")
            return
        self.db.remove_clip_by_id(first["id"])
        await ctx.send("Clip übersprungen (nächster Clip).")

    @commands.command(name="clearclips")
    async def clear_clips(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        count = self.db.clear_clips()
        await ctx.send(f"Clip-Queue geleert. {count} Clip(s) entfernt.")

    @commands.command(name="clipon")
    async def clip_on(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        self.db.set_setting("clip_enabled", "1")
        await ctx.send("Clip Requests sind jetzt AKTIVIERT.")

    @commands.command(name="clipoff")
    async def clip_off(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        self.db.set_setting("clip_enabled", "0")
        await ctx.send("Clip Requests sind jetzt DEAKTIVIERT.")

    # ------------------------------------------------------------------ #
    # Verlosung (Channel-Points-Raffle)
    # ------------------------------------------------------------------ #

    async def _on_redemption(self, event: dict):
        """EventSub meldet eine Channel-Point-Einloesung.

        Nur Einloesungen der aktiven Raffle-Belohnung zaehlen als Los (jede
        Einloesung = 1 Los, Mehrfach-Lose erlaubt).
        """
        if self.db.get_setting("raffle_open", "0") != "1":
            return
        reward_id = self.db.get_setting("raffle_reward_id", "")
        ev_reward = (event.get("reward") or {}).get("id")
        if not reward_id or ev_reward != reward_id:
            return
        added = self.db.add_raffle_entry(
            event.get("id", ""),
            reward_id,
            str(event.get("user_id", "")),
            event.get("user_login", ""),
            event.get("user_name", "") or event.get("user_login", ""),
        )
        if added:
            print(f"Verlosung: Los von @{event.get('user_login')} "
                  f"({self.db.raffle_entry_count()} Lose gesamt)")

    async def _raffle_reconcile(self):
        """Nach (Neu-)Start verpasste Einloesungen nachtragen.

        EventSub liefert nur Live-Events – war der Bot kurz offline, fehlen diese
        Lose. Daher beim Start die offenen Einloesungen der Belohnung nachladen.
        """
        if self.db.get_setting("raffle_open", "0") != "1":
            return
        reward_id = self.db.get_setting("raffle_reward_id", "")
        if not reward_id or not self.points:
            return
        try:
            redemptions = await self.points.list_redemptions(reward_id, "UNFULFILLED")
        except Exception as e:
            print(f"Verlosung: Reconcile-Fehler: {e}")
            return
        new = 0
        for r in redemptions:
            user = r.get("user_login", "")
            if self.db.add_raffle_entry(
                r.get("id", ""), reward_id, str(r.get("user_id", "")),
                user, r.get("user_name", "") or user,
            ):
                new += 1
        if new:
            print(f"Verlosung: {new} verpasste Lose nachgetragen.")

    @commands.command(name="raffle", aliases=["verlosung"])
    async def raffle(self, ctx: commands.Context, action: str = None, cost: str = None):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            await ctx.send(f"@{ctx.author.name} Nur Mods können die Verlosung steuern.")
            return
        if not self.points:
            await ctx.send(
                "Verlosung ist nicht eingerichtet (TWITCH_BC_REFRESH_TOKEN fehlt)."
            )
            return

        action = (action or "status").strip().lower()

        if action == "start":
            if self.db.get_setting("raffle_open", "0") == "1":
                await ctx.send("Es läuft bereits eine Verlosung. Erst !raffle draw oder !raffle cancel.")
                return
            try:
                price = int(cost) if cost and cost.isdigit() else self.raffle_default_cost
            except ValueError:
                price = self.raffle_default_cost
            self.db.clear_raffle()
            reward_id = await self.points.create_reward(self.raffle_title, price)
            if not reward_id:
                await ctx.send("Verlosung konnte nicht gestartet werden (Belohnung anlegen fehlgeschlagen).")
                return
            self.db.set_setting("raffle_reward_id", reward_id)
            self.db.set_setting("raffle_cost", str(price))
            self.db.set_setting("raffle_open", "1")
            await ctx.send(
                f"🎟️ Verlosung gestartet! Löse „{self.raffle_title}\" für {price} "
                f"Punkte ein, um ein Los zu kaufen (mehrere möglich). Viel Glück!"
            )

        elif action in ("stop", "close"):
            if self.db.get_setting("raffle_open", "0") != "1":
                await ctx.send("Aktuell läuft keine Verlosung.")
                return
            reward_id = self.db.get_setting("raffle_reward_id", "")
            await self.points.set_reward_paused(reward_id, True)
            self.db.set_setting("raffle_open", "0")
            await ctx.send(
                f"Verlosung geschlossen – keine neuen Lose mehr. "
                f"{self.db.raffle_entry_count()} Lose von {self.db.raffle_unique_count()} "
                f"Teilnehmern. Ziehung mit !raffle draw."
            )

        elif action in ("draw", "ziehen", "winner"):
            entries = self.db.get_raffle_entries()
            if not entries:
                await ctx.send("Keine Lose vorhanden – noch niemand hat teilgenommen.")
                return
            # Annahme automatisch schliessen, falls noch offen.
            if self.db.get_setting("raffle_open", "0") == "1":
                reward_id = self.db.get_setting("raffle_reward_id", "")
                await self.points.set_reward_paused(reward_id, True)
                self.db.set_setting("raffle_open", "0")
            winner = random.choice(entries)  # jedes Los = eine Zeile -> faire Gewichtung
            await ctx.send(
                f"🎉 Die Verlosung gewinnt: @{winner['user_name']}! "
                f"(aus {len(entries)} Losen von {self.db.raffle_unique_count()} Teilnehmern) "
                f"Glückwunsch! 🏆  — !raffle draw für neue Ziehung, !raffle end zum Abschließen."
            )

        elif action in ("end", "finish"):
            await self._raffle_finalize(ctx, refund=False)

        elif action in ("cancel", "abbrechen", "refund"):
            await self._raffle_finalize(ctx, refund=True)

        else:  # status
            open_now = self.db.get_setting("raffle_open", "0") == "1"
            count = self.db.raffle_entry_count()
            if not open_now and count == 0:
                await ctx.send(
                    "Keine aktive Verlosung. Start mit !raffle start [Punktekosten]."
                )
                return
            cost_s = self.db.get_setting("raffle_cost", "?")
            state = "offen" if open_now else "geschlossen"
            await ctx.send(
                f"🎟️ Verlosung ({state}): {count} Lose von "
                f"{self.db.raffle_unique_count()} Teilnehmern, {cost_s} Punkte pro Los. "
                f"Befehle: !raffle start/stop/draw/end/cancel"
            )

    async def _raffle_finalize(self, ctx: commands.Context, refund: bool):
        """Verlosung abschliessen. refund=True erstattet allen die Punkte
        (CANCELED), sonst bleiben die Punkte ausgegeben (FULFILLED).
        Anschliessend Belohnung loeschen und Lose leeren.
        """
        reward_id = self.db.get_setting("raffle_reward_id", "")
        if not reward_id and self.db.raffle_entry_count() == 0:
            await ctx.send("Aktuell läuft keine Verlosung.")
            return
        ids = self.db.get_raffle_redemption_ids()
        status = "CANCELED" if refund else "FULFILLED"
        if reward_id and ids:
            await self.points.update_redemptions(reward_id, ids, status)
        if reward_id:
            await self.points.delete_reward(reward_id)
        n = self.db.clear_raffle()
        self.db.set_setting("raffle_open", "0")
        self.db.set_setting("raffle_reward_id", "")
        if refund:
            await ctx.send(f"Verlosung abgebrochen. {n} Lose entfernt, Punkte wurden erstattet.")
        else:
            await ctx.send(f"Verlosung abgeschlossen. {n} Lose archiviert, Belohnung entfernt.")

    # ------------------------------------------------------------------ #
    # Mod-Controls
    # ------------------------------------------------------------------ #

    @commands.command(name="sron")
    async def sron(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        self.db.set_setting("sr_enabled", "1")
        await ctx.send("Song Requests sind jetzt AKTIVIERT.")

    @commands.command(name="sroff")
    async def sroff(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        self.db.set_setting("sr_enabled", "0")
        await ctx.send("Song Requests sind jetzt DEAKTIVIERT.")

    @commands.command(name="reactions", aliases=["reaktionen"])
    async def reactions(self, ctx: commands.Context, *, mode: str = None):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        mode = (mode or "").strip().lower()
        if mode in ("on", "an", "1"):
            self.db.set_setting("reactions_enabled", "1")
            await ctx.send("Chat-Reaktionen (Zufallsantworten & Emoji-Combos) AKTIVIERT.")
        elif mode in ("off", "aus", "0"):
            self.db.set_setting("reactions_enabled", "0")
            await ctx.send("Chat-Reaktionen DEAKTIVIERT.")
        else:
            state = "AN" if self.db.get_setting("reactions_enabled", "1") == "1" else "AUS"
            await ctx.send(f"Chat-Reaktionen sind {state}. Nutze: !reactions on/off")

    @commands.command(name="reloademotes")
    async def reload_emotes(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        if not self._room_id:
            await ctx.send("Channel-ID noch nicht bekannt – bitte gleich nochmal versuchen.")
            return
        names = await fetch_channel_emote_names(self._room_id)
        self.chat.set_word_emotes(names)
        await ctx.send(f"Emote-Listen neu geladen: {len(names)} BTTV/7TV-Emotes.")

    @commands.command(name="commands", aliases=["hilfe", "help", "befehle"])
    async def help_cmd(self, ctx: commands.Context):
        await ctx.send(
            "Für alle: !sr <Link> (Song) | !clip <Link> (Clip) | !queue | "
            "!clipqueue | !np (aktueller Song) | !streamlist | !wrongsong "
            "(eigener Song) | !wrongclip (eigener Clip) | !remove <#> (eigener "
            "Song) | !commands"
        )
        await ctx.send(
            "Nur Mods/Broadcaster: !skip | !clearqueue | !skipclip | !clearclips "
            "| !sron/!sroff (Songrequests an/aus) | !clipon/!clipoff (Clips an/aus) "
            "| !reactions on/off | !reloademotes | !raffle start/stop/draw/end/cancel "
            "(Channel-Points-Verlosung)"
        )


def main():
    bot = Bot()
    bot.run()


if __name__ == "__main__":
    main()
