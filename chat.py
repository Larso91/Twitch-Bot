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


class ChatExtras:
    """Zustandsbehaftete Logik für Emoji-Combos (keine Zufallsantworten mehr)."""

    def __init__(self, combo_threshold: int = 3):
        self.combo_threshold = max(2, combo_threshold)
        # Bekannte Wort-Emotes (BTTV/7TV), per API geladen.
        self.word_emotes: set = set()
        self._combo_key: Optional[str] = None
        self._combo_display: str = ""
        self._combo_count: int = 0
        self._combo_announced: bool = False

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
        self._combo_announced = False

    def _feed_emoji(self, display: str, key: str) -> Optional[str]:
        if key == self._combo_key:
            self._combo_count += 1
        else:
            self._combo_key = key
            self._combo_display = display
            self._combo_count = 1
            self._combo_announced = False
        # Nur EINMAL pro Combo ansagen (beim Erreichen der Schwelle) – kein
        # Nachlegen bei 6, 9, 12 ... mehr, damit der Bot den Chat nicht flutet.
        if self._combo_count >= self.combo_threshold and not self._combo_announced:
            self._combo_announced = True
            return f"{self._combo_display} x{self._combo_count} Combo! 🔥"
        return None

    def process(self, text: str, twitch_emotes: Optional[set] = None) -> Optional[str]:
        """Verarbeitet eine Chat-Nachricht und gibt ggf. eine Bot-Antwort zurück.

        - Emote-only-Nachrichten (Unicode-Emoji, Twitch-, BTTV- oder 7TV-Emote)
          zählen die Combo hoch.
        - Andere Nachrichten brechen die Combo (keine Zufallsantworten mehr).

        twitch_emotes: Namen der von Twitch in dieser Nachricht erkannten Emotes.
        """
        combo = self._parse_combo(text, twitch_emotes or set())
        if combo:
            return self._feed_emoji(*combo)
        # Kein reines Emoji -> laufende Combo ist beendet.
        self._reset_combo()
        return None
