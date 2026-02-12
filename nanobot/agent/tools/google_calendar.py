"""Google Calendar tool — read/write via service account."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/calendar"


class _ServiceAccountAuth:
    """Minimal Google service account token manager using JWT."""

    def __init__(self, credentials_path: str):
        self._creds_path = credentials_path
        self._creds: dict[str, Any] | None = None
        self._token: str | None = None
        self._expires_at: float = 0

    def _load_creds(self) -> dict[str, Any]:
        if self._creds is None:
            with open(self._creds_path) as f:
                self._creds = json.load(f)
        return self._creds

    async def get_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        import time

        if self._token and time.time() < self._expires_at - 60:
            return self._token

        creds = self._load_creds()
        now = int(time.time())

        # Build JWT
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": creds["client_email"],
            "scope": SCOPES,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }

        # Sign with RSA
        from nanobot.agent.tools._jwt_sign import rs256_sign

        token_jwt = rs256_sign(header, payload, creds["private_key"])

        # Exchange for access token
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": token_jwt,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        self._expires_at = now + data.get("expires_in", 3600)
        return self._token


class GoogleCalendarTool(Tool):
    """Tool to manage Google Calendar events."""

    name = "google_calendar"
    description = (
        "Manage Google Calendar events. "
        "Actions: list_events, add_event, update_event, delete_event. "
        "The main calendar is read-only. The taskmaster calendar supports read/write."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_events", "add_event", "update_event", "delete_event"],
                "description": "Action to perform",
            },
            "calendar": {
                "type": "string",
                "enum": ["main", "taskmaster"],
                "description": "Which calendar to use (default: main)",
            },
            "event_id": {
                "type": "string",
                "description": "Event ID (for update/delete)",
            },
            "name": {
                "type": "string",
                "description": "Event name/summary",
            },
            "start": {
                "type": "string",
                "description": "Start datetime in ISO 8601 format (e.g. 2025-01-15T09:00:00-05:00)",
            },
            "end": {
                "type": "string",
                "description": "End datetime in ISO 8601 format",
            },
            "location": {
                "type": "string",
                "description": "Event location (optional)",
            },
            "days_ahead": {
                "type": "integer",
                "description": "Days ahead to list events (default: 7)",
                "minimum": 1,
                "maximum": 60,
            },
            "days_behind": {
                "type": "integer",
                "description": "Days behind to list events (default: 1)",
                "minimum": 0,
                "maximum": 30,
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        credentials_path: str = "",
        main_calendar_id: str = "",
        taskmaster_calendar_id: str = "",
    ):
        self._credentials_path = credentials_path
        self._main_cal_id = main_calendar_id
        self._taskmaster_cal_id = taskmaster_calendar_id
        self._auth: _ServiceAccountAuth | None = None

    def _get_auth(self) -> _ServiceAccountAuth:
        if self._auth is None:
            self._auth = _ServiceAccountAuth(self._credentials_path)
        return self._auth

    def _calendar_id(self, calendar: str | None) -> str:
        if calendar == "taskmaster":
            return self._taskmaster_cal_id
        return self._main_cal_id

    async def execute(self, action: str, **kwargs: Any) -> str:
        if not self._credentials_path:
            return "Error: Google Calendar credentials path not configured"

        if not Path(self._credentials_path).exists():
            return f"Error: Credentials file not found: {self._credentials_path}"

        try:
            if action == "list_events":
                return await self._list_events(
                    calendar=kwargs.get("calendar", "main"),
                    days_ahead=kwargs.get("days_ahead", 7),
                    days_behind=kwargs.get("days_behind", 1),
                )
            elif action == "add_event":
                return await self._add_event(
                    calendar=kwargs.get("calendar", "taskmaster"),
                    name=kwargs.get("name"),
                    start=kwargs.get("start"),
                    end=kwargs.get("end"),
                    location=kwargs.get("location"),
                )
            elif action == "update_event":
                return await self._update_event(
                    calendar=kwargs.get("calendar", "taskmaster"),
                    event_id=kwargs.get("event_id"),
                    name=kwargs.get("name"),
                    start=kwargs.get("start"),
                    end=kwargs.get("end"),
                    location=kwargs.get("location"),
                )
            elif action == "delete_event":
                return await self._delete_event(
                    calendar=kwargs.get("calendar", "taskmaster"),
                    event_id=kwargs.get("event_id"),
                )
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Error: {e}"

    async def _headers(self) -> dict[str, str]:
        token = await self._get_auth().get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _list_events(
        self, calendar: str, days_ahead: int, days_behind: int,
    ) -> str:
        cal_id = self._calendar_id(calendar)
        if not cal_id:
            return f"Error: {calendar} calendar ID not configured"
        now = datetime.now().astimezone()
        time_min = (now - timedelta(days=days_behind)).isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{CALENDAR_API}/calendars/{cal_id}/events",
                headers=headers,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 50,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        events = data.get("items", [])
        if not events:
            return f"No events found on {calendar} calendar."

        lines = [f"{calendar.title()} calendar — {len(events)} event(s):\n"]
        for ev in events:
            summary = ev.get("summary", "(no title)")
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "?"))
            end = ev.get("end", {})
            end_str = end.get("dateTime", end.get("date", "?"))
            location = ev.get("location", "")
            event_id = ev.get("id", "")

            # Format datetime for readability
            try:
                dt = datetime.fromisoformat(start_str)
                display_start = dt.strftime("%a %b %d, %I:%M %p")
            except (ValueError, TypeError):
                display_start = start_str

            line = f"  {display_start} — {summary}"
            if location:
                line += f" @ {location}"
            line += f"\n    ID: {event_id}"
            lines.append(line)

        return "\n".join(lines)

    async def _add_event(
        self,
        calendar: str,
        name: str | None,
        start: str | None,
        end: str | None,
        location: str | None,
    ) -> str:
        if calendar != "taskmaster":
            return "Error: Can only add events to the taskmaster calendar"
        if not name:
            return "Error: event name is required"
        if not start or not end:
            return "Error: start and end datetimes are required"

        body: dict[str, Any] = {
            "summary": name,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if location:
            body["location"] = location

        cal_id = self._calendar_id("taskmaster")
        if not cal_id:
            return "Error: taskmaster calendar ID not configured"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CALENDAR_API}/calendars/{cal_id}/events",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            event = resp.json()

        return f"Created event: '{event.get('summary')}' (ID: {event.get('id')})"

    async def _update_event(
        self,
        calendar: str,
        event_id: str | None,
        name: str | None,
        start: str | None,
        end: str | None,
        location: str | None,
    ) -> str:
        if calendar != "taskmaster":
            return "Error: Can only update events on the taskmaster calendar"
        if not event_id:
            return "Error: event_id is required"

        body: dict[str, Any] = {}
        if name:
            body["summary"] = name
        if start:
            body["start"] = {"dateTime": start}
        if end:
            body["end"] = {"dateTime": end}
        if location is not None:
            body["location"] = location

        if not body:
            return "Error: at least one field to update is required"

        cal_id = self._calendar_id("taskmaster")
        if not cal_id:
            return "Error: taskmaster calendar ID not configured"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{CALENDAR_API}/calendars/{cal_id}/events/{event_id}",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            event = resp.json()

        return f"Updated event: '{event.get('summary')}' (ID: {event.get('id')})"

    async def _delete_event(
        self, calendar: str, event_id: str | None,
    ) -> str:
        if calendar != "taskmaster":
            return "Error: Can only delete events on the taskmaster calendar"
        if not event_id:
            return "Error: event_id is required"

        cal_id = self._calendar_id("taskmaster")
        if not cal_id:
            return "Error: taskmaster calendar ID not configured"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{CALENDAR_API}/calendars/{cal_id}/events/{event_id}",
                headers=headers,
            )
            if resp.status_code in (204, 404, 410):
                return f"Deleted event (ID: {event_id})"
            resp.raise_for_status()

        return f"Deleted event (ID: {event_id})"
