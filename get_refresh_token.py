"""Einmaliges Hilfsskript: holt einen YouTube OAuth Refresh Token.

Voraussetzung:
  1. Google Cloud Console -> APIs & Services -> YouTube Data API v3 aktiviert.
  2. OAuth-2.0-Client-ID vom Typ "Desktop app" erstellt (Client ID + Secret).
  3. OAuth-Zustimmungsbildschirm konfiguriert; Veröffentlichungsstatus auf
     "Produktion" setzen, damit der Refresh Token nicht nach 7 Tagen abläuft.

Aufruf (lokal auf deinem PC, NICHT auf Railway):
    python get_refresh_token.py

Es öffnet den Browser. Melde dich mit dem Google-Konto an, dem die Playlist
gehört, und bestätige den Zugriff. Bei der Warnung "App nicht verifiziert":
"Erweitert" -> "Weiter zu ... (unsicher)". Am Ende wird der Refresh Token
ausgegeben -> in Railway als YOUTUBE_REFRESH_TOKEN hinterlegen.
"""

import glob
import http.server
import json
import socket
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _load_credentials():
    """Liest Client ID/Secret aus einer client_secret*.json im Ordner, sonst per Eingabe."""
    files = glob.glob("client_secret*.json") + glob.glob("*credentials*.json")
    if files:
        try:
            with open(files[0], encoding="utf-8") as f:
                data = json.load(f)
            section = data.get("installed") or data.get("web") or {}
            cid = section.get("client_id", "").strip()
            secret = section.get("client_secret", "").strip()
            if cid and secret:
                print(f"Credentials geladen aus: {files[0]}")
                return cid, secret
        except Exception as e:
            print(f"Konnte {files[0]} nicht lesen ({e}), bitte manuell eingeben.")
    return input("Client ID: ").strip(), input("Client Secret: ").strip()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    client_id, client_secret = _load_credentials()
    if not client_id or not client_secret:
        print("Client ID und Secret sind erforderlich.")
        return

    port = _free_port()
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
                "<h2>Fertig! Du kannst dieses Fenster schliessen.</h2>".encode("utf-8")
            )

        def log_message(self, *args):
            pass

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

    server = http.server.HTTPServer(("localhost", port), Handler)
    print("\nOeffne Browser zur Anmeldung ...")
    print("Falls sich nichts oeffnet, diese URL manuell aufrufen:\n" + url + "\n")
    webbrowser.open(url)
    server.handle_request()  # wartet auf den Redirect von Google

    if holder.get("error"):
        print("Fehler bei der Anmeldung:", holder["error"])
        return
    code = holder.get("code")
    if not code:
        print("Kein Autorisierungscode erhalten.")
        return

    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as resp:
            tokens = json.loads(resp.read().decode())
    except Exception as e:
        print("Token-Austausch fehlgeschlagen:", e)
        return

    refresh = tokens.get("refresh_token")
    if refresh:
        print("\n=== ERFOLG ===")
        print("YOUTUBE_REFRESH_TOKEN=" + refresh)
        print("\nDiesen Wert in Railway als Variable hinterlegen.")
    else:
        print("Kein refresh_token erhalten. Antwort:", tokens)
        print(
            "Tipp: Unter https://myaccount.google.com/permissions den App-Zugriff "
            "entfernen und erneut versuchen."
        )


if __name__ == "__main__":
    main()
