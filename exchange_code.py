"""Tauscht einen OAuth-Authorization-Code gegen einen Refresh Token.

Schreibt den Refresh Token in refresh_token.txt (erscheint NICHT auf der
Konsole). Aufruf:  python exchange_code.py "<code>" "<redirect_uri>"
"""

import glob
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    if len(sys.argv) < 3:
        print("Aufruf: python exchange_code.py <code> <redirect_uri>")
        return
    code, redirect_uri = sys.argv[1], sys.argv[2]
    files = glob.glob("client_secret*.json") + glob.glob("*credentials*.json")
    if not files:
        print("Keine client_secret*.json gefunden.")
        return
    with open(files[0], encoding="utf-8") as f:
        d = json.load(f)
    s = d.get("installed") or d.get("web") or {}

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": s.get("client_id", ""),
        "client_secret": s.get("client_secret", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
        ) as r:
            tok = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode())
        print("FEHLER", e.code, "->", err.get("error"), "/", err.get("error_description"))
        print("(invalid_grant = Code abgelaufen oder schon benutzt -> neu generieren)")
        return

    rt = tok.get("refresh_token")
    if rt:
        with open("refresh_token.txt", "w", encoding="utf-8") as f:
            f.write(rt)
        print("ERFOLG: Refresh Token in refresh_token.txt gespeichert.")
        print("Laenge:", len(rt), "Zeichen | Scope:", tok.get("scope", "(?)"))
    else:
        print("Kein refresh_token in der Antwort. Felder:", list(tok.keys()))


if __name__ == "__main__":
    main()
