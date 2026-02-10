"""Google Maps tools — geocoding, place search, directions, and distance matrix."""

import json
from typing import Any
from urllib.parse import urlencode

import httpx

from nanobot.agent.tools.base import Tool


class GoogleMapsClient:
    """Shared HTTP client for Google Maps REST APIs."""

    BASE = "https://maps.googleapis.com/maps/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._http = httpx.AsyncClient(timeout=15)

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        params["key"] = self.api_key
        url = f"{self.BASE}/{endpoint}?{urlencode(params, doseq=True)}"
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.json()


class MapsGeocodeTool(Tool):
    """Geocode addresses or reverse-geocode coordinates."""

    def __init__(self, api_key: str):
        self._client = GoogleMapsClient(api_key)

    @property
    def name(self) -> str:
        return "maps_geocode"

    @property
    def description(self) -> str:
        return (
            "Convert an address or place name to coordinates (geocode), "
            "or convert coordinates to an address (reverse geocode)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Address or place name to geocode",
                },
                "lat": {
                    "type": "number",
                    "description": "Latitude for reverse geocoding",
                },
                "lng": {
                    "type": "number",
                    "description": "Longitude for reverse geocoding",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        address = kwargs.get("address")
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")

        if not address and lat is None:
            return "Error: provide 'address' for geocoding or 'lat'+'lng' for reverse geocoding."

        try:
            if address:
                data = await self._client._get("geocode/json", {"address": address})
            else:
                data = await self._client._get(
                    "geocode/json", {"latlng": f"{lat},{lng}"}
                )

            if data.get("status") != "OK":
                return f"Geocoding failed: {data.get('status')} — {data.get('error_message', '')}"

            results = []
            for r in data["results"][:3]:
                loc = r["geometry"]["location"]
                results.append({
                    "formatted_address": r["formatted_address"],
                    "lat": loc["lat"],
                    "lng": loc["lng"],
                    "place_id": r.get("place_id"),
                    "types": r.get("types", []),
                })
            return json.dumps(results, indent=2)
        except Exception as e:
            return f"Error: {e}"


class MapsSearchPlacesTool(Tool):
    """Search for places by text query or near a location."""

    def __init__(self, api_key: str):
        self._client = GoogleMapsClient(api_key)

    @property
    def name(self) -> str:
        return "maps_search_places"

    @property
    def description(self) -> str:
        return (
            "Search for places using a text query (e.g. 'coffee shops in Boston') "
            "or find places near coordinates. Returns names, addresses, ratings, "
            "and locations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text search query (e.g. 'pizza near Times Square')",
                },
                "lat": {
                    "type": "number",
                    "description": "Latitude for nearby search (requires lng and radius)",
                },
                "lng": {
                    "type": "number",
                    "description": "Longitude for nearby search",
                },
                "radius": {
                    "type": "integer",
                    "description": "Search radius in meters (default 5000, max 50000)",
                },
                "type": {
                    "type": "string",
                    "description": "Place type filter (e.g. 'restaurant', 'gas_station', 'hospital')",
                },
                "open_now": {
                    "type": "boolean",
                    "description": "Only return places that are open now",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query")
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")
        radius = kwargs.get("radius", 5000)
        place_type = kwargs.get("type")
        open_now = kwargs.get("open_now")

        if not query and lat is None:
            return "Error: provide 'query' for text search or 'lat'+'lng' for nearby search."

        try:
            if query:
                params: dict[str, Any] = {"query": query}
                if lat is not None and lng is not None:
                    params["location"] = f"{lat},{lng}"
                    params["radius"] = radius
                if place_type:
                    params["type"] = place_type
                if open_now:
                    params["opennow"] = ""
                data = await self._client._get("place/textsearch/json", params)
            else:
                params = {
                    "location": f"{lat},{lng}",
                    "radius": radius,
                }
                if place_type:
                    params["type"] = place_type
                if open_now:
                    params["opennow"] = ""
                data = await self._client._get("place/nearbysearch/json", params)

            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                return f"Search failed: {data.get('status')} — {data.get('error_message', '')}"

            results = []
            for p in data.get("results", [])[:10]:
                loc = p.get("geometry", {}).get("location", {})
                entry: dict[str, Any] = {
                    "name": p.get("name"),
                    "address": p.get("formatted_address") or p.get("vicinity"),
                    "lat": loc.get("lat"),
                    "lng": loc.get("lng"),
                    "place_id": p.get("place_id"),
                }
                if p.get("rating"):
                    entry["rating"] = p["rating"]
                    entry["total_ratings"] = p.get("user_ratings_total", 0)
                if p.get("opening_hours"):
                    entry["open_now"] = p["opening_hours"].get("open_now")
                if p.get("price_level") is not None:
                    entry["price_level"] = p["price_level"]
                if p.get("types"):
                    entry["types"] = p["types"][:5]
                results.append(entry)

            if not results:
                return "No places found."
            return json.dumps(results, indent=2)
        except Exception as e:
            return f"Error: {e}"


class MapsDirectionsTool(Tool):
    """Get directions and routes between locations."""

    def __init__(self, api_key: str):
        self._client = GoogleMapsClient(api_key)

    @property
    def name(self) -> str:
        return "maps_directions"

    @property
    def description(self) -> str:
        return (
            "Get directions between two locations. Returns step-by-step directions, "
            "distance, duration, and route summary. Supports driving, walking, bicycling, "
            "and transit modes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Starting point (address, place name, or 'lat,lng')",
                },
                "destination": {
                    "type": "string",
                    "description": "Ending point (address, place name, or 'lat,lng')",
                },
                "mode": {
                    "type": "string",
                    "enum": ["driving", "walking", "bicycling", "transit"],
                    "description": "Travel mode (default: driving)",
                },
                "waypoints": {
                    "type": "string",
                    "description": "Intermediate stops, pipe-separated (e.g. 'place1|place2')",
                },
                "avoid": {
                    "type": "string",
                    "description": "Features to avoid: tolls, highways, ferries (pipe-separated)",
                },
                "alternatives": {
                    "type": "boolean",
                    "description": "Return alternative routes",
                },
                "units": {
                    "type": "string",
                    "enum": ["metric", "imperial"],
                    "description": "Unit system (default: imperial)",
                },
            },
            "required": ["origin", "destination"],
        }

    async def execute(self, **kwargs: Any) -> str:
        origin = kwargs["origin"]
        destination = kwargs["destination"]
        mode = kwargs.get("mode", "driving")
        units = kwargs.get("units", "imperial")

        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "units": units,
        }
        if kwargs.get("waypoints"):
            params["waypoints"] = kwargs["waypoints"]
        if kwargs.get("avoid"):
            params["avoid"] = kwargs["avoid"]
        if kwargs.get("alternatives"):
            params["alternatives"] = "true"

        try:
            data = await self._client._get("directions/json", params)

            if data.get("status") != "OK":
                return f"Directions failed: {data.get('status')} — {data.get('error_message', '')}"

            routes = []
            for route in data["routes"][:3]:
                legs = []
                for leg in route["legs"]:
                    steps = []
                    for step in leg["steps"]:
                        # Strip HTML tags from instructions
                        instr = step.get("html_instructions", "")
                        import re
                        instr = re.sub(r"<[^>]+>", " ", instr).strip()
                        instr = re.sub(r"\s+", " ", instr)
                        steps.append({
                            "instruction": instr,
                            "distance": step["distance"]["text"],
                            "duration": step["duration"]["text"],
                        })
                    legs.append({
                        "start_address": leg.get("start_address"),
                        "end_address": leg.get("end_address"),
                        "distance": leg["distance"]["text"],
                        "duration": leg["duration"]["text"],
                        "duration_in_traffic": leg.get("duration_in_traffic", {}).get("text"),
                        "steps": steps,
                    })
                routes.append({
                    "summary": route.get("summary"),
                    "legs": legs,
                    "warnings": route.get("warnings", []),
                })

            return json.dumps(routes, indent=2)
        except Exception as e:
            return f"Error: {e}"


class MapsDistanceMatrixTool(Tool):
    """Compute travel distance and time between multiple origins and destinations."""

    def __init__(self, api_key: str):
        self._client = GoogleMapsClient(api_key)

    @property
    def name(self) -> str:
        return "maps_distance_matrix"

    @property
    def description(self) -> str:
        return (
            "Calculate travel distance and time between one or more origins and destinations. "
            "Useful for comparing routes or finding the closest location."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "origins": {
                    "type": "string",
                    "description": "Origin locations, pipe-separated (addresses or 'lat,lng')",
                },
                "destinations": {
                    "type": "string",
                    "description": "Destination locations, pipe-separated",
                },
                "mode": {
                    "type": "string",
                    "enum": ["driving", "walking", "bicycling", "transit"],
                    "description": "Travel mode (default: driving)",
                },
                "units": {
                    "type": "string",
                    "enum": ["metric", "imperial"],
                    "description": "Unit system (default: imperial)",
                },
            },
            "required": ["origins", "destinations"],
        }

    async def execute(self, **kwargs: Any) -> str:
        params: dict[str, Any] = {
            "origins": kwargs["origins"],
            "destinations": kwargs["destinations"],
            "mode": kwargs.get("mode", "driving"),
            "units": kwargs.get("units", "imperial"),
        }

        try:
            data = await self._client._get("distancematrix/json", params)

            if data.get("status") != "OK":
                return f"Distance matrix failed: {data.get('status')} — {data.get('error_message', '')}"

            results = []
            for i, origin in enumerate(data.get("origin_addresses", [])):
                row = data["rows"][i]
                for j, dest in enumerate(data.get("destination_addresses", [])):
                    el = row["elements"][j]
                    entry: dict[str, Any] = {
                        "origin": origin,
                        "destination": dest,
                        "status": el["status"],
                    }
                    if el["status"] == "OK":
                        entry["distance"] = el["distance"]["text"]
                        entry["duration"] = el["duration"]["text"]
                        if "duration_in_traffic" in el:
                            entry["duration_in_traffic"] = el["duration_in_traffic"]["text"]
                    results.append(entry)

            return json.dumps(results, indent=2)
        except Exception as e:
            return f"Error: {e}"


class MapsPlaceDetailsTool(Tool):
    """Get detailed information about a specific place."""

    def __init__(self, api_key: str):
        self._client = GoogleMapsClient(api_key)

    @property
    def name(self) -> str:
        return "maps_place_details"

    @property
    def description(self) -> str:
        return (
            "Get detailed information about a place by its place_id. "
            "Returns hours, phone, website, reviews, and more."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "place_id": {
                    "type": "string",
                    "description": "Google Maps place ID (from search results)",
                },
            },
            "required": ["place_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        place_id = kwargs["place_id"]
        fields = (
            "name,formatted_address,formatted_phone_number,website,"
            "rating,user_ratings_total,price_level,opening_hours,"
            "geometry,types,business_status,url,reviews"
        )

        try:
            data = await self._client._get(
                "place/details/json",
                {"place_id": place_id, "fields": fields},
            )

            if data.get("status") != "OK":
                return f"Place details failed: {data.get('status')} — {data.get('error_message', '')}"

            r = data["result"]
            loc = r.get("geometry", {}).get("location", {})

            result: dict[str, Any] = {
                "name": r.get("name"),
                "address": r.get("formatted_address"),
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "phone": r.get("formatted_phone_number"),
                "website": r.get("website"),
                "google_maps_url": r.get("url"),
                "rating": r.get("rating"),
                "total_ratings": r.get("user_ratings_total"),
                "price_level": r.get("price_level"),
                "business_status": r.get("business_status"),
                "types": r.get("types", []),
            }

            hours = r.get("opening_hours")
            if hours:
                result["open_now"] = hours.get("open_now")
                result["hours"] = hours.get("weekday_text", [])

            reviews = r.get("reviews", [])
            if reviews:
                result["reviews"] = [
                    {
                        "author": rv.get("author_name"),
                        "rating": rv.get("rating"),
                        "text": rv.get("text", "")[:200],
                        "time": rv.get("relative_time_description"),
                    }
                    for rv in reviews[:3]
                ]

            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"
