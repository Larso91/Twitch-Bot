"""Holt einen Twitch OAuth Refresh Token fuer die Channel-Points-Verwaltung.

Dieser Token gehoert zu DEINEM HAUPT-ACCOUNT (dem Channel-Inhaber) – NICHT zum
Bot-Account. Nur der Broadcaster darf Belohnungen verwalten und Einloesungen
erstatten. Scope: channel:manage:redemptions.

Aufruf in einem OFFENEN PowerShell-Fenster (nicht per Doppelklick):

    cd C:\\Users\\Gangz\\twitchbot
    python get_twitch_token.py

Diese Version braucht KEINEN lokalen Webserver und KEINEN freien Port. Sie nutzt
die ohnehin in der Twitch-Console eingetragene Redirect-URL (Standard:
https://localhost). Ablauf:

    1. Skript zeigt eine Login-URL und oeffnet sie im Browser.
    2. Du meldest dich mit deinem HAUPT-ACCOUNT an und bestaetigst.
    3. Twitch leitet auf https://localhost/?code=... weiter. Dort laeuft nichts,
       die Seite laedt NICHT ("Verbindung fehlgeschlagen") – das ist normal.
    4. Kopiere die KOMPLETTE Adresse aus der Browser-Adressleiste und fuege sie
       hier ein. Das Skript zieht sich den Code selbst heraus.

Falls deine registrierte Redirect-URL anders lautet (z.B. http://localhost),
vor dem Start setzen:  $env:TWITCH_REDIRECT_URI = 'http://localhost'

Der Refresh Token wird in twitch_refresh_token.txt geschrieben. Inhalt kopieren
und auf Railway als TWITCH_BC_REFRESH_TOKEN eintragen (BC = Broadcaster).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
SCOPE = "channel:manage:redemptions"
# Muss ZEICHENGENAU einer in der Twitch-Console registrierten Redirect-URL
# entsprechen. Default: https://localhost (Standard-Eintrag der meisten Apps).
REDIRECT_URI = os.environ.get("TWITCH_REDIRECT_URI", "https://localhost").strip()


def extract_code(raw: str) -> str:
    """Holt den ?code=... Wert aus einer eingefuegten URL (oder nimmt den
    rohen Code, falls direkt eingefuegt)."""
    raw = raw.strip()
    if "?" in raw or "code=" in raw or "error=" in raw:
        query = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
        params = urllib.parse.parse_qs(query)
        if params.get("error"):
            print("Twitch meldete einen Fehler:", params.get("error_description", params["error"])[0])
            return ""
        return params.get("code", [""])[0]
    return raw  # vermutlich direkt der Code


def main():
    print("=" * 60)
    print(" Twitch Refresh Token holen (Broadcaster / Channel Points)")
    print("=" * 60)

    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if not client_id:
        client_id = input("TWITCH_CLIENT_ID: ").strip()
    if not client_secret:
        client_secret = input("TWITCH_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        print("FEHLER: Client-ID und Secret werden benoetigt.")
        input("\nEnter zum Schliessen ...")
        return

    print(f"\nRedirect-URL: {REDIRECT_URI}")
    print("(Muss exakt einem Eintrag in der Twitch-Console entsprechen.)")

    auth = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "force_verify": "true",
    })

    print("\n--> Im Browser mit deinem HAUPT-ACCOUNT (Channel-Inhaber) anmelden:")
    print("\n" + auth + "\n")
    try:
        import webbrowser

        webbrowser.open(auth)
    except Exception:
        pass

    print("Nach dem Bestaetigen landest du auf einer Seite, die NICHT laedt")
    print(f"({REDIRECT_URI}/?code=...). Das ist normal.")
    print("--> Kopiere die KOMPLETTE Adresse aus der Browser-Adressleiste.\n")
    pasted = input("Hier einfuegen und Enter: ").strip()
    code = extract_code(pasted)
    if not code:
        print("\nKein Code erkannt. Bitte erneut versuchen.")
        input("\nEnter zum Schliessen ...")
        return

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as resp:
            tokens = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print("\nToken-Austausch fehlgeschlagen:", e.code, e.read().decode()[:300])
        print("Haeufige Ursache: redirect_uri stimmt nicht 1:1 mit der Console")
        print("ueberein, oder der Code wurde schon benutzt (dann neu anmelden).")
        input("\nEnter zum Schliessen ...")
        return

    refresh = tokens.get("refresh_token")
    if refresh:
        with open("twitch_refresh_token.txt", "w", encoding="utf-8") as f:
            f.write(refresh)
        print("\n" + "=" * 60)
        print(" ERFOLG!")
        print("=" * 60)
        print(" Refresh Token wurde in twitch_refresh_token.txt geschrieben.")
        print(" -> Inhalt kopieren, auf Railway als TWITCH_BC_REFRESH_TOKEN eintragen.")
        print(f"\n Scope: {tokens.get('scope', '(?)')}")
    else:
        print("\nKein refresh_token erhalten. Felder:", list(tokens.keys()))

    input("\nEnter zum Schliessen ...")


if __name__ == "__main__":
    main()
