"""Exist.io personal analytics tool."""

from typing import Any

import httpx

from nanobot.agent.tools.base import Tool

BASE_URL = "https://exist.io/api/2"


class ExistTool(Tool):
    """Tool to query and update Exist.io personal tracking data."""

    name = "exist"
    description = (
        "Query and update Exist.io personal analytics data. "
        "Actions: get_attributes, get_attribute, get_insights, "
        "get_correlations, get_averages, update_attribute, "
        "increment_attribute, create_attribute"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_attributes",
                    "get_attribute",
                    "get_insights",
                    "get_correlations",
                    "get_averages",
                    "update_attribute",
                    "increment_attribute",
                    "create_attribute",
                ],
                "description": "Action to perform",
            },
            "attribute": {
                "type": "string",
                "description": "Attribute name (e.g. 'steps', 'mood', 'sleep')",
            },
            "group": {
                "type": "string",
                "description": "Attribute group filter (e.g. 'activity', 'productivity', 'custom')",
            },
            "value": {
                "type": "number",
                "description": "Value to set or increment by",
            },
            "date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format (defaults to today)",
            },
            "days": {
                "type": "integer",
                "description": "Number of days of history (for get_attributes)",
                "minimum": 1,
                "maximum": 30,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results",
                "minimum": 1,
                "maximum": 100,
            },
            "label": {
                "type": "string",
                "description": "Display label for new attribute (create_attribute)",
            },
            "value_type": {
                "type": "integer",
                "description": "Value type for new attribute: 0=quantity, 1=decimal, 3=duration(min), 5=percentage, 7=boolean, 8=scale(1-9)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    async def execute(self, action: str, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: Exist.io API key not configured"

        try:
            if action == "get_attributes":
                return await self._get_attributes(
                    group=kwargs.get("group"),
                    days=kwargs.get("days", 1),
                    limit=kwargs.get("limit", 20),
                )
            elif action == "get_attribute":
                return await self._get_attribute(kwargs.get("attribute"))
            elif action == "get_insights":
                return await self._get_insights(kwargs.get("limit", 10))
            elif action == "get_correlations":
                return await self._get_correlations(
                    attribute=kwargs.get("attribute"),
                    limit=kwargs.get("limit", 20),
                )
            elif action == "get_averages":
                return await self._get_averages(
                    attribute=kwargs.get("attribute"),
                    limit=kwargs.get("limit", 20),
                )
            elif action == "update_attribute":
                return await self._update_attribute(
                    name=kwargs.get("attribute"),
                    value=kwargs.get("value"),
                    date=kwargs.get("date"),
                )
            elif action == "increment_attribute":
                return await self._increment_attribute(
                    name=kwargs.get("attribute"),
                    value=kwargs.get("value", 1),
                )
            elif action == "create_attribute":
                return await self._create_attribute(
                    label=kwargs.get("label"),
                    value_type=kwargs.get("value_type", 0),
                    group=kwargs.get("group", "custom"),
                )
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Error: {e}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _get_attributes(
        self, group: str | None, days: int, limit: int,
    ) -> str:
        params: dict[str, Any] = {"limit": limit, "days": days}
        if group:
            params["groups"] = group

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE_URL}/attributes/with-values/",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return "No attributes found."

        lines = [f"Found {len(results)} attribute(s):\n"]
        for attr in results:
            name = attr.get("name", "?")
            label = attr.get("label", name)
            values = attr.get("values", [])
            current = values[0].get("value", "N/A") if values else "N/A"
            group_name = attr.get("group", {}).get("name", "?") if isinstance(attr.get("group"), dict) else attr.get("group", "?")
            lines.append(f"  {label} ({name}): {current}  [{group_name}]")

        return "\n".join(lines)

    async def _get_attribute(self, name: str | None) -> str:
        if not name:
            return "Error: attribute name is required"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE_URL}/attributes/with-values/",
                headers=self._headers(),
                params={"attributes": name, "days": 1, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return f"Attribute '{name}' not found."

        attr = results[0]
        label = attr.get("label", name)
        values = attr.get("values", [])
        current = values[0].get("value", "N/A") if values else "N/A"
        date = values[0].get("date", "today") if values else "today"

        return f"{label} ({name}): {current} (as of {date})"

    async def _get_insights(self, limit: int) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE_URL}/insights/",
                headers=self._headers(),
                params={"limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return "No insights available."

        lines = [f"Recent insights ({len(results)}):\n"]
        for insight in results:
            text = insight.get("text", "")
            created = insight.get("created", "")
            lines.append(f"  - {text}")
            if created:
                lines.append(f"    ({created})")

        return "\n".join(lines)

    async def _get_correlations(
        self, attribute: str | None, limit: int,
    ) -> str:
        params: dict[str, Any] = {"limit": limit, "confident": "true"}
        if attribute:
            params["attribute"] = attribute

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE_URL}/correlations/",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return "No correlations found."

        lines = [f"Correlations ({len(results)}):\n"]
        for corr in results:
            attr1 = corr.get("attribute", "?")
            attr2 = corr.get("attribute2", "?")
            stars = corr.get("stars", 0)
            relationship = corr.get("second_person", "")
            star_str = "*" * stars
            lines.append(f"  {attr1} <-> {attr2} [{star_str}] {relationship}")

        return "\n".join(lines)

    async def _get_averages(
        self, attribute: str | None, limit: int,
    ) -> str:
        params: dict[str, Any] = {"limit": limit}
        if attribute:
            params["attribute"] = attribute

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE_URL}/averages/",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return "No averages available."

        lines = [f"Averages ({len(results)}):\n"]
        for avg in results:
            attr_name = avg.get("attribute", "?")
            overall = avg.get("overall", "N/A")
            lines.append(f"  {attr_name}: {overall} (overall)")

        return "\n".join(lines)

    async def _update_attribute(
        self, name: str | None, value: Any, date: str | None,
    ) -> str:
        if not name:
            return "Error: attribute name is required"
        if value is None:
            return "Error: value is required"

        payload = [{"name": name, "value": value}]
        if date:
            payload[0]["date"] = date

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}/attributes/update/",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()

        date_str = date or "today"
        return f"Updated {name} = {value} for {date_str}"

    async def _increment_attribute(
        self, name: str | None, value: Any,
    ) -> str:
        if not name:
            return "Error: attribute name is required"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}/attributes/increment/",
                headers=self._headers(),
                json=[{"name": name, "value": value}],
            )
            resp.raise_for_status()

        return f"Incremented {name} by {value}"

    async def _create_attribute(
        self, label: str | None, value_type: int, group: str,
    ) -> str:
        if not label:
            return "Error: label is required to create an attribute"

        type_names = {
            0: "quantity", 1: "decimal", 3: "duration",
            5: "percentage", 7: "boolean", 8: "scale",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}/attributes/create/",
                headers=self._headers(),
                json=[{
                    "label": label,
                    "value_type": value_type,
                    "group": group,
                    "manual": True,
                }],
            )
            resp.raise_for_status()
            data = resp.json()

        results = data if isinstance(data, list) else [data]
        if results:
            name = results[0].get("name", label)
            return f"Created attribute '{name}' ({type_names.get(value_type, 'unknown')}) in group '{group}'"
        return f"Created attribute '{label}'"
