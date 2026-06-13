import random
import re
from typing import Optional, Tuple

# ------------------------------------------------------------------ #
# Emoji-Erkennung
# ------------------------------------------------------------------ #

# Unicode-Bereiche, die als Emoji gelten (die wichtigsten/gängigsten).
_EMOJI_RANGES = (
    "\U0001F300-\U0001F5FF"  # Symbole & Piktogramme
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F680-\U0001F6FF"  # Transport & Karten
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"  # Ergänzende Symbole
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"  # Diverse Symbole
    "\U00002700-\U000027BF"  # Dingbats (inkl. ❤ U+2764)
    "\U0001F1E6-\U0001F1FF"  # Flaggen (Regional Indicators)
    "\U00002300-\U000023FF"  # z.B. ⏰ ⌛
    "\U00002B00-\U00002BFF"  # z.B. ⭐
    "\U0001F000-\U0001F0FF"
)

# Modifikatoren: Hautfarben, Variation Selectors, Zero-Width-Joiner.
_SKIN = "\U0001F3FB-\U0001F3FF"
_VS = "︎️"
_ZWJ = "‍"

# Erkennt ein einzelnes Emoji- oder Modifikator-Zeichen.
_ANY_EMOJI = re.compile(f"[{_EMOJI_RANGES}{_SKIN}{_VS}{_ZWJ}]")

# Erkennt ein vollständiges Emoji-"Cluster" (Basis + Modifikatoren + ZWJ-Ketten).
_BASE = f"[{_EMOJI_RANGES}]"
_MODS = f"[{_SKIN}{_VS}]*"
_CLUSTER = re.compile(f"(?:{_BASE}{_MODS}(?:{_ZWJ}{_BASE}{_MODS})*)")

# Zum Normalisieren des Vergleichsschlüssels (Modifikatoren entfernen).
_STRIP_MODS = re.compile(f"[{_SKIN}{_VS}{_ZWJ}]")


def parse_emoji_combo(text: str) -> Optional[Tuple[str, str]]:
    """Prüft, ob eine Nachricht nur aus EINEM (ggf. wiederholten) Emoji besteht.

    Gibt (anzeige_emoji, vergleichs_schlüssel) zurück, sonst None.
    Beispiele: "❤️" -> ("❤️", "❤"),  "🔥🔥🔥" -> ("🔥", "🔥"),
               "lol ❤️" -> None (enthält Text),  "❤️😂" -> None (zwei verschiedene).
    """
    stripped = "".join(text.split())
    if not stripped:
        return None
    # Bleibt nach Entfernen aller Emoji-Zeichen noch etwas übrig -> echter Text.
    if _ANY_EMOJI.sub("", stripped):
        return None
    clusters = _CLUSTER.findall(stripped)
    if not clusters:
        return None
    keys = {_STRIP_MODS.sub("", c) for c in clusters}
    if len(keys) == 1:
        return clusters[0], next(iter(keys))
    return None


# ------------------------------------------------------------------ #
# Zufällige Antworten
# ------------------------------------------------------------------ #

# Mix aus lockeren Sprüchen und kurzen Hype-Reaktionen.
# Einfach erweitern oder anpassen.
_RESPONSES = [
    # Lustige / lockere Sprüche
    "Haha, der war gut! 😂",
    "Das musste jetzt mal gesagt werden 😄",
    "Big Brain Moment 🧠",
    "Ich hab's gehört, ich hab's gehört 👀",
    "Da ist was dran, nicht schlecht!",
    "Klassiker im Chat 😎",
    "Genau mein Humor 😂",
    "Ohne Worte... aber trotzdem gut!",
    # Hype / Reaktionen
    "POGGERS! 🎉",
    "Let's gooo! 🚀",
    "Stark! 💪",
    "LOL 😆",
    "Das war clean!",
    "Chat ist heute on fire 🔥",
    "GG! 🏆",
    "Hype im Chat! 🙌",
    "Sauber gemacht!",
    "W im Chat 👑",
]


class ChatExtras:
    """Zustandsbehaftete Logik für Emoji-Combos und zufällige Antworten."""

    def __init__(self, reply_chance: float = 0.15, combo_threshold: int = 3):
        self.reply_chance = reply_chance
        self.combo_threshold = max(2, combo_threshold)
        # Bekannte Wort-Emotes (BTTV/7TV), per API geladen.
        self.word_emotes: set = set()
        self._combo_key: Optional[str] = None
        self._combo_display: str = ""
        self._combo_count: int = 0

    def set_word_emotes(self, names: set) -> None:
        self.word_emotes = set(names)

    def _parse_combo(self, text: str, twitch_emotes: set) -> Optional[Tuple[str, str]]:
        # 1) Reines Unicode-Emoji (❤️ 🔥 ...)?
        unicode_combo = parse_emoji_combo(text)
        if unicode_combo:
            return unicode_combo
        # 2) Wort-Emote (Kappa, PogChamp, BTTV/7TV ...)?
        #    Nachricht muss nur aus EINEM (ggf. wiederholten) Emote-Wort bestehen.
        tokens = text.split()
        if not tokens:
            return None
        first = tokens[0]
        if all(t == first for t in tokens):
            if first in twitch_emotes or first in self.word_emotes:
                return first, first
        return None

    def _reset_combo(self) -> None:
        self._combo_key = None
        self._combo_display = ""
        self._combo_count = 0

    def _feed_emoji(self, display: str, key: str) -> Optional[str]:
        if key == self._combo_key:
            self._combo_count += 1
        else:
            self._combo_key = key
            self._combo_display = display
            self._combo_count = 1
        # Bei Erreichen der Schwelle ansagen, danach an jedem weiteren
        # Vielfachen (z.B. 3, 6, 9 ...) – so kein Spam bei jeder Nachricht.
        if (
            self._combo_count >= self.combo_threshold
            and self._combo_count % self.combo_threshold == 0
        ):
            return f"{self._combo_display} x{self._combo_count} Combo! 🔥"
        return None

    def process(self, text: str, twitch_emotes: Optional[set] = None) -> Optional[str]:
        """Verarbeitet eine Chat-Nachricht und gibt ggf. eine Bot-Antwort zurück.

        - Emote-only-Nachrichten (Unicode-Emoji, Twitch-, BTTV- oder 7TV-Emote)
          zählen die Combo hoch.
        - Andere Nachrichten brechen die Combo und lösen ggf. eine Zufallsantwort aus.

        twitch_emotes: Namen der von Twitch in dieser Nachricht erkannten Emotes.
        """
        combo = self._parse_combo(text, twitch_emotes or set())
        if combo:
            return self._feed_emoji(*combo)
        # Kein reines Emoji -> laufende Combo ist beendet.
        self._reset_combo()
        if random.random() < self.reply_chance:
            return random.choice(_RESPONSES)
        return None
