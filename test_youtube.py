"""Diagnose-Skript: testet die YouTube-OAuth-Verbindung und den Playlist-Zugriff.

Gibt KEINE Geheimnisse aus, nur Diagnose-Ergebnisse (Statuscodes, Fehlertexte,
Kanal-/Playlist-Titel). Lokal in einem Terminal ausfuehren:

    python test_youtube.py

Du wirst nach Refresh Token und Playlist-ID gefragt (aus Railway kopieren).
Client ID/Secret werden automatisch aus der client_secret*.json gelesen.
"""

import glob
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"


def load_client():
    files = glob.glob("client_secret*.json") + glob.glob("*credentials*.json")
    if files:
        with open(files[0], encoding="utf-8") as f:
            d = json.load(f)
        s = d.get("installed") or d.get("web") or {}
        cid, secret = s.get("client_id", ""), s.get("client_secret", "")
        if cid and secret:
            print(f"Client-Daten geladen aus: {files[0]}")
            return cid, secret
    return input("Client ID: ").strip(), input("Client Secret: ").strip()


def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body)) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _reason(err):
    try:
        return err["error"]["errors"][0].get("reason", "")
    except Exception:
        return ""


def main():
    cid, secret = load_client()
    refresh = (os.environ.get("YOUTUBE_REFRESH_TOKEN") or input("Refresh Token: ")).strip()
    playlist = (os.environ.get("YOUTUBE_PLAYLIST_ID") or input("Playlist ID: ")).strip()
    print()
    print("Getestete Playlist-ID:", repr(playlist))
    if playlist.startswith("http") or "list=" in playlist or " " in playlist:
        print("  ACHTUNG: Sieht nach ganzer URL/Leerzeichen aus - es darf NUR die ID rein (z.B. PLxxxx).")

    # 1) Access Token via Refresh Token
    print("\n[1] Access Token holen ...")
    st, j = _post(TOKEN_URL, {
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if st != 200 or "access_token" not in j:
        print("  FEHLER", st, "->", j.get("error"), "/", j.get("error_description"))
        print("  => Refresh Token oder Client ID/Secret stimmen nicht zusammen.")
        return
    token = j["access_token"]
    print("  OK - Access Token erhalten.")
    print("  Freigegebene Scopes:", j.get("scope", "(unbekannt)"))
    if "youtube" not in j.get("scope", ""):
        print("  ACHTUNG: Kein youtube-Scope! Refresh Token mit falschem Scope erzeugt.")

    # 2) Eigener Kanal
    print("\n[2] Eigenen YouTube-Kanal abfragen ...")
    st, j = _get(f"{API}/channels?part=snippet&mine=true", token)
    if st == 200 and j.get("items"):
        print("  OK - Angemeldeter Kanal:", j["items"][0]["snippet"]["title"])
    else:
        print("  WARNUNG", st, "->", j.get("error", {}).get("message"))
        print("  => Das Google-Konto hat evtl. keinen YouTube-Kanal.")

    # 3) Playlist pruefen
    print("\n[3] Playlist pruefen ...")
    st, j = _get(f"{API}/playlists?part=snippet&id={playlist}", token)
    if st == 200 and j.get("items"):
        print("  OK - Playlist gefunden:", j["items"][0]["snippet"]["title"])
    else:
        print("  PROBLEM", st, "->", j.get("error", {}).get("message", "(leer)"))
        print("  => Playlist nicht gefunden. Haeufig: falsche ID ODER die Playlist")
        print("     gehoert NICHT dem oben angemeldeten Kanal (man kann nur EIGENE")
        print("     Playlists bearbeiten).")

    # 4) Test-Insert (+ sofort wieder entfernen)
    print("\n[4] Test: Song einfuegen und wieder entfernen ...")
    body = json.dumps({
        "snippet": {
            "playlistId": playlist,
            "resourceId": {"kind": "youtube#video", "videoId": "M7lc1UVf-VE"},
        }
    }).encode()
    req = urllib.request.Request(
        f"{API}/playlistItems?part=snippet", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            item_id = json.loads(r.read().decode()).get("id")
        print("  OK - Einfuegen funktioniert!")
        dreq = urllib.request.Request(
            f"{API}/playlistItems?id={item_id}", method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            urllib.request.urlopen(dreq)
            print("  OK - Entfernen funktioniert ebenfalls.")
        except urllib.error.HTTPError as e:
            print("  Einfuegen OK, aber Entfernen-FEHLER", e.code)
        print("\n  ===> ALLES OK. Falls der Bot trotzdem meckert: Variablennamen in")
        print("       Railway pruefen (exakt YOUTUBE_REFRESH_TOKEN / YOUTUBE_PLAYLIST_ID).")
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode())
        print("  FEHLER", e.code, "| reason:", _reason(err))
        print("  Meldung:", err.get("error", {}).get("message", ""))
        print("\n  Haeufige Ursachen:")
        print("   - playlistNotFound  : Playlist-ID falsch oder gehoert anderem Konto")
        print("   - quotaExceeded     : YouTube-Tageslimit erreicht")
        print("   - insufficientPermissions / forbidden : falscher Scope oder Konto")


if __name__ == "__main__":
    main()
