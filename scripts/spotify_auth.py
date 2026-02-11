"""One-shot Spotify OAuth2 flow to get a refresh token."""

import http.server
import json
import os
import threading
import urllib.parse
import webbrowser

import urllib.request

REDIRECT_URI = "http://localhost:8080"
SCOPES = "user-modify-playback-state user-read-playback-state playlist-read-private playlist-read-collaborative streaming"
TOKEN_URL = "https://accounts.spotify.com/api/token"

auth_code = None
server_done = threading.Event()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Got it! You can close this tab.</h1>")
        server_done.set()

    def log_message(self, *args):
        pass  # silence logs


def main():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id:
        print("ERROR: Missing SPOTIFY_CLIENT_ID environment variable.")
        print("Set it and rerun, for example:")
        print("  export SPOTIFY_CLIENT_ID='your-spotify-app-client-id'")
        return
    if not client_secret:
        print("ERROR: Missing SPOTIFY_CLIENT_SECRET environment variable.")
        print("Set it and rerun, for example:")
        print("  export SPOTIFY_CLIENT_SECRET='your-spotify-app-client-secret'")
        return

    # Start local server to catch the redirect
    server = http.server.HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    # Open browser for auth
    auth_url = (
        "https://accounts.spotify.com/authorize?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
        })
    )
    print(f"Opening browser for Spotify auth...\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    print("Waiting for redirect...")
    server_done.wait(timeout=120)
    server.server_close()

    if not auth_code:
        print("ERROR: No auth code received.")
        return

    # Exchange code for tokens
    post_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=post_data)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    print(f"\nAccess Token:  {data['access_token'][:40]}...")
    print(f"Refresh Token: {data['refresh_token']}")
    print(f"Expires In:    {data.get('expires_in')}s")
    print(f"Scope:         {data.get('scope')}")
    print(f"\n>>> Copy this refresh token to your nanobot config <<<")


if __name__ == "__main__":
    main()
