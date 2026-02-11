"""Spotify playback control tool."""

import time
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class SpotifyTool(Tool):
    """Tool to control Spotify playback and search music."""

    name = "spotify"
    description = (
        "Control Spotify playback and search for music. "
        "Actions: now_playing, search, play, pause, queue, skip, previous, "
        "volume, devices, playlists"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "now_playing",
                    "search",
                    "play",
                    "pause",
                    "queue",
                    "skip",
                    "previous",
                    "volume",
                    "devices",
                    "playlists",
                ],
                "description": "Action to perform",
            },
            "query": {
                "type": "string",
                "description": "Search query (for search action)",
            },
            "uri": {
                "type": "string",
                "description": "Spotify URI to play or queue (e.g. spotify:track:xxx)",
            },
            "device_id": {
                "type": "string",
                "description": "Target device ID (optional, uses active device if omitted)",
            },
            "volume_percent": {
                "type": "integer",
                "description": "Volume level 0-100 (for volume action)",
                "minimum": 0,
                "maximum": 100,
            },
            "limit": {
                "type": "integer",
                "description": "Max results for search/playlists",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self._expires_at: float = 0

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600)
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def execute(self, action: str, **kwargs: Any) -> str:
        if not self.refresh_token:
            return "Error: Spotify not configured (missing refresh_token)"

        try:
            if action == "now_playing":
                return await self._now_playing()
            elif action == "search":
                return await self._search(
                    kwargs.get("query"), kwargs.get("limit", 5),
                )
            elif action == "play":
                return await self._play(
                    kwargs.get("uri"), kwargs.get("device_id"),
                )
            elif action == "pause":
                return await self._pause(kwargs.get("device_id"))
            elif action == "queue":
                return await self._queue(
                    kwargs.get("uri"), kwargs.get("device_id"),
                )
            elif action == "skip":
                return await self._skip(kwargs.get("device_id"))
            elif action == "previous":
                return await self._previous(kwargs.get("device_id"))
            elif action == "volume":
                return await self._volume(
                    kwargs.get("volume_percent"), kwargs.get("device_id"),
                )
            elif action == "devices":
                return await self._devices()
            elif action == "playlists":
                return await self._playlists(kwargs.get("limit", 20))
            else:
                return f"Unknown action: {action}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return "Error: No active Spotify device found. Start Spotify on a device first."
            return f"Spotify API error: {e.response.status_code} {e.response.text[:200]}"
        except Exception as e:
            return f"Error: {e}"

    async def _now_playing(self) -> str:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/me/player/currently-playing",
                headers=headers,
            )
            if resp.status_code == 204:
                return "Nothing currently playing."
            resp.raise_for_status()
            data = resp.json()

        item = data.get("item", {})
        if not item:
            return "Nothing currently playing."

        name = item.get("name", "Unknown")
        artists = ", ".join(a.get("name", "?") for a in item.get("artists", []))
        album = item.get("album", {}).get("name", "")
        is_playing = data.get("is_playing", False)
        progress = data.get("progress_ms", 0) // 1000
        duration = item.get("duration_ms", 0) // 1000
        uri = item.get("uri", "")

        status = "Playing" if is_playing else "Paused"
        return (
            f"{status}: {name} — {artists}\n"
            f"Album: {album}\n"
            f"Progress: {progress // 60}:{progress % 60:02d} / {duration // 60}:{duration % 60:02d}\n"
            f"URI: {uri}"
        )

    async def _search(self, query: str | None, limit: int) -> str:
        if not query:
            return "Error: search query is required"

        headers = await self._headers()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/search",
                headers=headers,
                params={"q": query, "type": "track", "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        tracks = data.get("tracks", {}).get("items", [])
        if not tracks:
            return f"No results for '{query}'"

        lines = [f"Search results for '{query}':\n"]
        for i, track in enumerate(tracks, 1):
            name = track.get("name", "?")
            artists = ", ".join(a.get("name", "?") for a in track.get("artists", []))
            uri = track.get("uri", "")
            lines.append(f"  {i}. {name} — {artists}")
            lines.append(f"     URI: {uri}")

        return "\n".join(lines)

    async def _play(self, uri: str | None, device_id: str | None) -> str:
        headers = await self._headers()
        params: dict[str, str] = {}
        if device_id:
            params["device_id"] = device_id

        body: dict[str, Any] = {}
        if uri:
            if uri.startswith("spotify:track:"):
                body["uris"] = [uri]
            else:
                body["context_uri"] = uri

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{API_BASE}/me/player/play",
                headers=headers,
                params=params,
                json=body if body else None,
            )
            resp.raise_for_status()

        if uri:
            return f"Playing: {uri}"
        return "Resumed playback"

    async def _pause(self, device_id: str | None) -> str:
        headers = await self._headers()
        params: dict[str, str] = {}
        if device_id:
            params["device_id"] = device_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{API_BASE}/me/player/pause",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

        return "Playback paused"

    async def _queue(self, uri: str | None, device_id: str | None) -> str:
        if not uri:
            return "Error: URI is required to add to queue"

        headers = await self._headers()
        params: dict[str, str] = {"uri": uri}
        if device_id:
            params["device_id"] = device_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{API_BASE}/me/player/queue",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

        return f"Added to queue: {uri}"

    async def _skip(self, device_id: str | None) -> str:
        headers = await self._headers()
        params: dict[str, str] = {}
        if device_id:
            params["device_id"] = device_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{API_BASE}/me/player/next",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

        return "Skipped to next track"

    async def _previous(self, device_id: str | None) -> str:
        headers = await self._headers()
        params: dict[str, str] = {}
        if device_id:
            params["device_id"] = device_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{API_BASE}/me/player/previous",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

        return "Went to previous track"

    async def _volume(self, percent: int | None, device_id: str | None) -> str:
        if percent is None:
            return "Error: volume_percent is required"

        headers = await self._headers()
        params: dict[str, Any] = {"volume_percent": percent}
        if device_id:
            params["device_id"] = device_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{API_BASE}/me/player/volume",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

        return f"Volume set to {percent}%"

    async def _devices(self) -> str:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/me/player/devices",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        devices = data.get("devices", [])
        if not devices:
            return "No Spotify devices found. Open Spotify on a device first."

        lines = [f"Spotify devices ({len(devices)}):\n"]
        for dev in devices:
            name = dev.get("name", "?")
            dtype = dev.get("type", "?")
            active = " (active)" if dev.get("is_active") else ""
            volume = dev.get("volume_percent", "?")
            did = dev.get("id", "")
            lines.append(f"  {name} [{dtype}]{active} — vol {volume}%")
            lines.append(f"    ID: {did}")

        return "\n".join(lines)

    async def _playlists(self, limit: int) -> str:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/me/playlists",
                headers=headers,
                params={"limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        playlists = data.get("items", [])
        if not playlists:
            return "No playlists found."

        lines = [f"Your playlists ({len(playlists)}):\n"]
        for pl in playlists:
            name = pl.get("name", "?")
            tracks = pl.get("tracks", {}).get("total", 0)
            uri = pl.get("uri", "")
            lines.append(f"  {name} ({tracks} tracks)")
            lines.append(f"    URI: {uri}")

        return "\n".join(lines)
