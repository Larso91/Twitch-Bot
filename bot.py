import asyncio
import os

from twitchio.ext import commands

from chat import ChatExtras
from database import Database
from emotes import fetch_channel_emote_names, twitch_emote_names
from jokes import get_random_joke
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
        self.db = Database()
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

    async def event_ready(self):
        print(f"Bot gestartet: {self.nick}")
        print(f"Kanal:         {self.channel_name}")
        print(f"Max. Länge:    {self.max_duration} Min.")
        if self.yt_api_key:
            print("Längencheck:   AKTIV (YouTube API Key gefunden)")
        else:
            print("Längencheck:   INAKTIV (kein YOUTUBE_API_KEY gesetzt)")
        print(f"Witze-Interval:{self.joke_interval // 60} Min.")
        print(f"Zufallsantworten: {int(self.chat.reply_chance * 100)}% | Combo ab {self.chat.combo_threshold}")
        asyncio.create_task(self._joke_loop())

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

        position = self.db.add_song(url, info["title"], ctx.author.name)
        self._write_streamlist_file()
        if position <= 1:
            hint = "spielt als Nächstes"
        else:
            hint = f"spielt als Nächstes (Platz #{position})"
        await ctx.send(f"@{ctx.author.name} Hinzugefügt — {hint}: {info['title']}")

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
        song = self.db.get_last_by_requester(ctx.author.name)
        if not song:
            await ctx.send(f"@{ctx.author.name} Du hast keinen Song in der Queue.")
            return
        self.db.remove_by_id(song["id"])
        await ctx.send(f"@{ctx.author.name} Song entfernt: {song['title']}")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context):
        songs = self.db.get_queue()
        if not songs:
            await ctx.send("Die Queue ist leer. Füge mit !sr <YouTube-Link> Songs hinzu!")
            return
        total = len(songs)
        entries = " | ".join(
            f"#{i + 1} {s['title']} (@{s['requester']})" for i, s in enumerate(songs[:5])
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
        song = self.db.get_first()
        if not song:
            await ctx.send("Aktuell kein Song in der Queue.")
            return
        await ctx.send(
            f"Aktueller Song: {song['title']} (von @{song['requester']}) -> {song['url']}"
        )

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            await ctx.send(f"@{ctx.author.name} Nur Mods können Songs überspringen.")
            return
        song = self.db.remove_first()
        if song:
            await ctx.send(f"Übersprungen: {song['title']}")
        else:
            await ctx.send("Die Queue ist leer.")

    @commands.command(name="remove", aliases=["sr_remove"])
    async def remove(self, ctx: commands.Context, *, position: str = None):
        if not position or not position.strip().isdigit():
            await ctx.send(f"@{ctx.author.name} Benutze: !remove <Position>")
            return
        pos = int(position.strip())
        songs = self.db.get_queue()
        if pos < 1 or pos > len(songs):
            await ctx.send(
                f"@{ctx.author.name} Position {pos} existiert nicht (Queue: {len(songs)} Songs)."
            )
            return
        song = songs[pos - 1]
        is_mod = ctx.author.is_mod or ctx.author.is_broadcaster
        is_own = song["requester"].lower() == ctx.author.name.lower()
        if not is_mod and not is_own:
            await ctx.send(f"@{ctx.author.name} Du kannst nur deine eigenen Songs entfernen.")
            return
        self.db.remove_by_id(song["id"])
        await ctx.send(f"@{ctx.author.name} Entfernt: {song['title']}")

    @commands.command(name="clearqueue", aliases=["clearsr"])
    async def clearqueue(self, ctx: commands.Context):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        count = self.db.clear_queue()
        await ctx.send(f"Queue geleert. {count} Song(s) entfernt.")

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

    @commands.command(name="commands", aliases=["hilfe", "help"])
    async def help_cmd(self, ctx: commands.Context):
        await ctx.send(
            "Befehle: !sr <Link> | !wrongsong | !queue | !np | !streamlist | "
            "!remove <#> | [Mod] !skip | !clearqueue | !sron | !sroff | "
            "!reactions on/off | !reloademotes"
        )


def main():
    bot = Bot()
    bot.run()


if __name__ == "__main__":
    main()
