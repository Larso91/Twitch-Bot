import random
from collections import deque
from typing import Optional

import aiohttp

# Fallback-Witze falls die API nicht erreichbar ist
_FALLBACK = [
    "Warum können Geister so schlecht lügen? Weil man durch sie hindurchsieht.",
    "Was ist orange und läuft durch den Wald? Ein Wanderine.",
    "Ich habe heute meinen Diätplan gebrochen. Er war aus Schokolade.",
    "Was sagt ein Tal zum anderen Tal? Moin Moin.",
    "Warum hat der Mathe-Lehrer das Fenster aufgemacht? Weil er Durchzug brauchte.",
    "Was ist ein Keks unter einem Baum? Ein schattiges Plätzchen.",
    "Wie nennt man einen Bumerang, der nicht zurückkommt? Stock.",
    "Wer hat Angst vor dem schwarzen Mann? Der weiße Schimmel.",
]

_API_URL = (
    "https://v2.jokeapi.dev/joke/Miscellaneous,Puns,Spooky,Christmas"
    "?lang=de&blacklistFlags=nsfw,explicit,racist,sexist"
)


class JokeProvider:
    """Liefert Witze und vermeidet Wiederholungen.

    - Merkt sich die zuletzt erzählten Witze (history) und gibt keinen davon
      erneut aus, solange genug Auswahl vorhanden ist.
    - Holt Witze von der JokeAPI; bei einem Treffer aus der history wird
      mehrmals neu angefragt.
    - Die Fallback-Witze werden als gemischter "Stapel" durchgespielt, bevor
      sich ein Witz wiederholt.
    """

    def __init__(self, history_size: int = 25, api_retries: int = 5):
        # history_size: wie viele zuletzt erzählte Witze gemerkt werden
        self._recent = deque(maxlen=history_size)
        self._recent_set = set()
        self._api_retries = api_retries
        self._fallback_bag: list = []

    def _remember(self, joke: str) -> None:
        if len(self._recent) == self._recent.maxlen and self._recent:
            oldest = self._recent[0]
            # Wird gleich durch das append verdrängt -> aus dem Set entfernen,
            # sofern nicht mehrfach vorhanden.
            if oldest not in list(self._recent)[1:]:
                self._recent_set.discard(oldest)
        self._recent.append(joke)
        self._recent_set.add(joke)

    def _is_recent(self, joke: str) -> bool:
        return joke in self._recent_set

    def _next_fallback(self) -> str:
        """Fallback-Witz aus gemischtem Stapel, ohne Wiederholung."""
        if not self._fallback_bag:
            self._fallback_bag = _FALLBACK[:]
            random.shuffle(self._fallback_bag)
        # Bevorzugt einen, der nicht in der history ist.
        for i, joke in enumerate(self._fallback_bag):
            if not self._is_recent(joke):
                return self._fallback_bag.pop(i)
        return self._fallback_bag.pop()

    async def _fetch_api(self) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _API_URL, timeout=aiohttp.ClientTimeout(total=6)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if data.get("type") == "single":
                        return data["joke"]
                    if data.get("type") == "twopart":
                        return f'{data["setup"]} — {data["delivery"]}'
        except Exception:
            return None
        return None

    async def get_joke(self) -> str:
        # Mehrere Versuche, einen noch nicht erzählten Witz von der API zu holen.
        for _ in range(self._api_retries):
            joke = await self._fetch_api()
            if joke and not self._is_recent(joke):
                self._remember(joke)
                return joke
        # API nicht erreichbar oder nur Wiederholungen -> Fallback-Stapel.
        joke = self._next_fallback()
        self._remember(joke)
        return joke


# Geteilte Instanz, damit sich die history über alle Aufrufe hinweg merkt.
_provider = JokeProvider()


async def get_random_joke() -> str:
    """Fetch a German joke without repeating recent ones."""
    return await _provider.get_joke()
