"""Automatisches Wörterrätsel für den Twitch-Chat.

Der Bot postet in zufälligen Abständen ein deutsches Wort mit
vertauschten Buchstaben. Der Chat muss es erraten. Wer es zuerst
richtig tippt, wird genannt und das nächste Rätsel startet sofort.

Ablauf pro Runde:
  1. Bot postet das Rätsel  (z.B. "🔤 Wörterrätsel: NRÜHIGG – was ist das Wort?")
  2. Nach HINT_SECONDS ohne Lösung: Hinweis (erster Buchstabe)
  3. Nach SOLVE_SECONDS ohne Lösung: Auflösung + Pause + neues Rätsel

Konfiguration (Env-Variablen, alle optional):
  WORDGAME_MIN_MINUTES   Minimale Pause zwischen Rätseln (Standard: 10)
  WORDGAME_MAX_MINUTES   Maximale Pause zwischen Rätseln (Standard: 20)
  WORDGAME_HINT_SECONDS  Sekunden bis zum ersten Buchstaben-Hinweis (Standard: 60)
  WORDGAME_SOLVE_SECONDS Sekunden bis zur Auflösung (Standard: 120)
"""

import asyncio
import os
import random
from typing import Optional


# ---------------------------------------------------------------------------
# Wortliste – gemischtes Deutsch, gut ratbar im Chat
# ---------------------------------------------------------------------------
WORDS = [
    # Tiere
    "Elefant", "Giraffe", "Pinguin", "Krokodil", "Schmetterling",
    "Schildkroete", "Nashorn", "Flamingo", "Delfin", "Chamäleon",
    "Erdmaennchen", "Schnabeltier", "Orang-Utan", "Koalabär", "Bisamratte",
    "Axolotl", "Qualle", "Seepferdchen", "Kapuziner", "Wombat",
    # Essen & Trinken
    "Spaghetti", "Schnitzel", "Brezel", "Kartoffel", "Apfelkuchen",
    "Erdbeere", "Schokolade", "Ananas", "Blaubeere", "Himbeere",
    "Kaesekuchen", "Tiramisu", "Gulasch", "Lebkuchen", "Marzipan",
    "Zuckerwatte", "Pfannkuchen", "Weintraube", "Kohlrabi", "Holunderblüte",
    # Natur & Wetter
    "Regenbogen", "Gewitter", "Schneeflocke", "Wasserfall", "Vulkan",
    "Sonnenuntergang", "Blitzschlag", "Nordlicht", "Sanddüne", "Lavastrom",
    "Gletscher", "Monsun", "Tsunami", "Hurrikan", "Stalagmit",
    # Berufe & Alltag
    "Feuerwehr", "Astronaut", "Tischler", "Bibliothekar", "Zauberer",
    "Schornsteinfeger", "Imker", "Geigenbauer", "Uhrmacher", "Forscher",
    # Technik & Sonstiges
    "Computer", "Drucker", "Satellit", "Roboter", "Mikroskop",
    "Teleskop", "Zeppelin", "Leuchtturm", "Kaleidoskop", "Fernrohr",
    # Sport & Freizeit
    "Trampolin", "Jongleur", "Skateboard", "Kajak", "Fallschirm",
    "Bogenschiessen", "Billard", "Drachenfliegen", "Tauchgang", "Bumerang",
    # Kultur & Spass
    "Abenteuer", "Geheimnis", "Zirkus", "Piraten", "Drachen",
    "Dschungel", "Labyrinth", "Phantom", "Schatzsuche", "Detektiv",
    "Vampir", "Mumie", "Kobold", "Einhorn", "Drache",
    # Musik
    "Schlagzeug", "Klarinette", "Fagott", "Akkordeon", "Triangel",
    "Didgeridoo", "Theremin", "Marimba", "Kazoo", "Tamburin",
    # Laenger / schwieriger
    "Donaudampfschiff", "Zwetschgenknödel", "Streichholzschachtel",
    "Zungenbrecher", "Handschuhfach", "Eichhörnchen", "Schmetterlingsblüte",
    "Kugelschreiber", "Sonnencreme", "Taschenmesser",
]


def scramble(word: str) -> str:
    """Buchstaben des Wortes zufällig vertauschen (mind. 1 Umsortierung)."""
    letters = list(word.upper())
    # Bei sehr kurzen Wörtern (≤2 Buchstaben) gibt es keine sinnvolle Vertauschung.
    if len(letters) <= 2:
        return "".join(letters)
    for _ in range(50):          # max. 50 Versuche, damit kein Endlos-Loop
        random.shuffle(letters)
        if "".join(letters) != word.upper():
            return "".join(letters)
    return "".join(letters)


class WordGame:
    """Zustandsbehaftete Spiellogik – wird einmal pro Bot-Instanz angelegt."""

    def __init__(
        self,
        min_minutes: int = 10,
        max_minutes: int = 20,
        hint_seconds: int = 60,
        solve_seconds: int = 120,
    ):
        self.min_minutes = min_minutes
        self.max_minutes = max(min_minutes, max_minutes)
        self.hint_seconds = hint_seconds
        self.solve_seconds = solve_seconds

        self._current_word: Optional[str] = None   # Lösung (Originalschreibweise)
        self._scrambled: Optional[str] = None       # Angezeigtes Rätsel
        self._hint_given: bool = False
        self._active: bool = False                  # Läuft gerade ein Rätsel?
        self._round_task: Optional[asyncio.Task] = None

        # Zuletzt verwendete Wörter nicht sofort wiederholen
        self._recent: list = []
        self._recent_max = min(20, len(WORDS) // 2)

    @classmethod
    def from_env(cls) -> "WordGame":
        return cls(
            min_minutes=int(os.environ.get("WORDGAME_MIN_MINUTES", "10")),
            max_minutes=int(os.environ.get("WORDGAME_MAX_MINUTES", "20")),
            hint_seconds=int(os.environ.get("WORDGAME_HINT_SECONDS", "60")),
            solve_seconds=int(os.environ.get("WORDGAME_SOLVE_SECONDS", "120")),
        )

    def _pick_word(self) -> str:
        pool = [w for w in WORDS if w not in self._recent]
        if not pool:
            pool = WORDS[:]
        word = random.choice(pool)
        self._recent.append(word)
        if len(self._recent) > self._recent_max:
            self._recent.pop(0)
        return word

    def check_guess(self, text: str) -> bool:
        """Gibt True zurück, wenn `text` die aktuelle Lösung ist (case-insensitiv)."""
        if not self._active or not self._current_word:
            return False
        return text.strip().lower() == self._current_word.lower()

    async def run_loop(self, send_fn):
        """Dauerschleife: Pause -> Rätsel -> Hinweis -> Auflösung -> repeat.

        `send_fn(text)` ist eine async-Funktion, die eine Nachricht in den
        Twitch-Chat schickt.
        """
        # Kurze Aufwärmphase damit der Bot beim Start nicht sofort postet.
        await asyncio.sleep(30)
        while True:
            try:
                # --- Pause zwischen den Rätseln ---
                wait = random.randint(self.min_minutes * 60,
                                      self.max_minutes * 60)
                await asyncio.sleep(wait)

                # --- Neues Rätsel starten ---
                word = self._pick_word()
                scrambled = scramble(word)
                self._current_word = word
                self._scrambled = scrambled
                self._hint_given = False
                self._active = True

                await send_fn(
                    f"🔤 Wörterrätsel! Welches deutsche Wort versteckt sich hier? "
                    f"» {scrambled} «  (Tipp direkt in den Chat!)"
                )

                # --- Hinweis nach hint_seconds ---
                await asyncio.sleep(self.hint_seconds)
                if not self._active:
                    continue   # wurde schon gelöst
                self._hint_given = True
                hint = word[0].upper() + ("_" * (len(word) - 1))
                await send_fn(
                    f"💡 Hinweis: Das Wort beginnt mit » {word[0].upper()} « "
                    f"– Muster: {hint}"
                )

                # --- Auflösung nach solve_seconds ---
                remaining = self.solve_seconds - self.hint_seconds
                await asyncio.sleep(max(0, remaining))
                if not self._active:
                    continue   # wurde zwischenzeitlich gelöst
                self._active = False
                await send_fn(
                    f"⏰ Zeit abgelaufen! Die Lösung war: » {word} « – "
                    f"nächstes Rätsel kommt bald!"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Worträtsel-Fehler: {e}")
                await asyncio.sleep(10)

    def solve(self):
        """Wird aufgerufen, wenn jemand richtig geraten hat – deaktiviert die Runde."""
        self._active = False

    @property
    def current_word(self) -> Optional[str]:
        return self._current_word if self._active else None

    @property
    def scrambled(self) -> Optional[str]:
        return self._scrambled if self._active else None
