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
    "Was macht ein Clown im Büro? Faxen.",
    "Was ist grün und klopft an die Tür? Ein Klopfsalat.",
    "Wie nennt man einen dicken Vegetarier? Biotonne.",
    "Was ist gelb und kann nicht schwimmen? Ein Bagger.",
    "Treffen sich zwei Magneten. Sagt der eine: 'Was soll ich heute bloß anziehen?'",
    "Was sitzt auf einem Baum und ruft 'Aha'? Ein Uhu mit Sprachfehler.",
    "Wie nennt man einen Keks, der über die Straße rennt? Ein Renekloede.",
    "Was ist rot und steht am Straßenrand? Eine Blutwurst, der schlecht ist.",
    "Warum gehen Ameisen nicht in die Kirche? Weil sie In-Sekten sind.",
    "Was ist weiß und stört beim Essen? Eine Lawine.",
    "Wie heißt der schnellste Keks der Welt? Husarn — äh, ein Husarenkrapfen.",
    "Was macht ein Pirat am Computer? Er drückt die Enter-Taste.",
    "Was ist braun und schaut durchs Schlüsselloch? Ein Spionkuchen.",
    "Warum nehmen Bienen so viel Honig? Weil sie keine Sümme machen können.",
    "Was liegt am Strand und spricht undeutlich? Eine Nuschel.",
    "Was ist klein, braun und läuft durch den Wald? Eine Wandernuss.",
    "Wie nennt man eine Gruppe Wale, die ein Instrument spielt? Eine Orca-ster.",
    "Was sagt der große Stift zum kleinen Stift? Wachs-mal-stift.",
    "Warum war die Mathebuch traurig? Es hatte zu viele Probleme.",
    "Was ist ein Cowboy ohne Pferd? Ein Sattelschlepper.",
    "Welcher Tag ist bei den Fischen am beliebtesten? Der Freitag.",
    "Was ist schwarz-weiß und kommt nicht vom Fleck? Ein Zeb-Stau.",
    "Wie nennt man einen Bären ohne Ohren? B.",
    "Was macht eine Wolke mit Juckreiz? Sie geht zum Wolkenkratzer.",
    "Warum können Seeräuber den Alphabet-Anfang nicht? Weil sie immer beim 'C' bleiben.",
    "Was ist der Unterschied zwischen einem Keks und einem Stein? Mit einem Stein kann man keinen Kaffee dippen.",
    "Was sitzt im Wald und winkt? Ein Huhn-derwasser.",
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
