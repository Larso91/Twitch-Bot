import random
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


async def get_random_joke() -> str:
    """Fetch a German joke from JokeAPI, fallback to local list."""
    url = "https://v2.jokeapi.dev/joke/Miscellaneous,Puns,Spooky,Christmas?lang=de&blacklistFlags=nsfw,explicit,racist,sexist"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 200:
                    return random.choice(_FALLBACK)
                data = await resp.json()
                if data.get("type") == "single":
                    return data["joke"]
                if data.get("type") == "twopart":
                    return f'{data["setup"]} — {data["delivery"]}'
    except Exception:
        pass
    return random.choice(_FALLBACK)
