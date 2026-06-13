"""Holt einen YouTube OAuth Refresh Token (robuste Version).

Der Token wird in refresh_token.txt geschrieben (erscheint NICHT auf der
Konsole). Aufruf in einem OFFENEN PowerShell-Fenster (nicht per Doppelklick):

    cd C:\\Users\\Gangz\\twitchbot
    python get_refresh_token.py

Falls Windows beim Start nach der Firewall fragt -> "Zugriff zulassen".
"""

import glob
import http.server
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def load_client():
    files = glob.glob("client_secret*.json") + glob.glob("*credentials*.json")
    if not files:
        print("FEHLER: Keine client_secret*.json im Ordner gefunden.")
        return None, None
    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)
    s = data.get("installed") or data.get("web") or {}
    print(f"Client-Daten geladen aus: {files[0]}")
    return s.get("client_id", "").strip(), s.get("client_secret", "").strip()


def free_port():
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    print("=" * 55)
    print(" YouTube Refresh Token holen")
    print("=" * 55)
    client_id, client_secret = load_client()
    if not client_id or not client_secret:
        input("\nEnter zum Schliessen ...")
        return

    port = free_port()
    redirect_uri = f"http://localhost:{port}/"
    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            holder["code"] = params.get("code", [None])[0]
            holder["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Fertig! Du kannst dieses Fenster schliessen und zum Terminal zurueck.</h2>".encode("utf-8")
            )

        def log_message(self, *args):
            pass

    auth = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })

    server = http.server.HTTPServer(("localhost", port), Handler)
    print("\n--> Im Browser anmelden (mit dem Konto, dem die Playlist gehoert).")
    print("    Bei 'App nicht verifiziert': Erweitert -> Weiter zu ... (unsicher).")
    print("\nFalls sich kein Browser oeffnet, diese URL manuell aufrufen:\n")
    print(auth + "\n")
    try:
        webbrowser.open(auth)
    except Exception:
        pass
    print("Warte auf die Anmeldung im Browser ...")
    server.handle_request()  # blockiert bis zum Redirect

    if holder.get("error"):
        print("\nFEHLER bei der Anmeldung:", holder["error"])
        input("\nEnter zum Schliessen ...")
        return
    code = holder.get("code")
    if not code:
        print("\nKein Autorisierungscode empfangen.")
        input("\nEnter zum Schliessen ...")
        return

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as resp:
            tokens = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print("\nToken-Austausch fehlgeschlagen:", e.code, e.read().decode()[:200])
        input("\nEnter zum Schliessen ...")
        return

    refresh = tokens.get("refresh_token")
    if refresh:
        with open("refresh_token.txt", "w", encoding="utf-8") as f:
            f.write(refresh)
        print("\n" + "=" * 55)
        print(" ERFOLG!")
        print("=" * 55)
        print(" Der Refresh Token wurde in die Datei refresh_token.txt")
        print(" geschrieben (im selben Ordner).")
        print(" -> Datei oeffnen, Inhalt kopieren, in Railway als")
        print("    YOUTUBE_REFRESH_TOKEN eintragen.")
        print(f"\n Laenge: {len(refresh)} Zeichen | Scope: {tokens.get('scope','(?)')}")
    else:
        print("\nKein refresh_token erhalten. Antwortfelder:", list(tokens.keys()))
        print("Tipp: https://myaccount.google.com/permissions -> App-Zugriff")
        print("entfernen und erneut versuchen.")

    input("\nEnter zum Schliessen ...")


if __name__ == "__main__":
    main()
