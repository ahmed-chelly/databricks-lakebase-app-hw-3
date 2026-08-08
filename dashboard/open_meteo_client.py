"""
Open-Meteo weather adapter.

Thin wrapper around Open-Meteo's free, unauthenticated APIs
(https://open-meteo.com/) - no API key, no signup, no Databricks secret
needed. All HTTP calls and response parsing live here so the MCP tool
functions in weather_mcp_server.py stay thin.

Two endpoints are used:
    - Geocoding API: resolves a free-text place name to latitude/longitude.
    - Forecast API: current conditions + a multi-day daily forecast, in
      imperial units (Fahrenheit, mph, inches) for readability.
"""

import re
from datetime import datetime

import requests

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10

# "41.85,-87.65" - lets callers skip geocoding and pass coordinates directly.
_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# WMO weather interpretation codes (https://open-meteo.com/en/docs), the only
# format Open-Meteo returns conditions in - there is no free-text field.
_WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow fall",
    73: "moderate snow fall",
    75: "heavy snow fall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


class WeatherLookupError(Exception):
    """Raised for a bad/unresolvable location or an Open-Meteo API failure."""


def _describe_weather_code(code: int | None) -> str:
    if code is None:
        return "unknown"
    return _WMO_CODES.get(int(code), f"unknown (code {code})")


def _request(url: str, params: dict) -> dict:
    try:
        response = requests.get(url, params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise WeatherLookupError(f"Weather API request failed: {e}") from e


def resolve_location(location: str) -> dict:
    """
    Resolve free text (city name) or a "lat,lon" pair into coordinates.

    Args:
        location: A city name (e.g. "Austin, TX"), or "latitude,longitude".

    Returns:
        A dict with latitude, longitude, and display_name.

    Raises:
        WeatherLookupError: if the location can't be resolved or the
            geocoding API call fails.
    """
    coord_match = _COORD_RE.match(location)
    if coord_match:
        lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
        return {"latitude": lat, "longitude": lon, "display_name": f"{lat},{lon}"}

    data = _request(_GEOCODING_URL, {"name": location, "count": 1})
    results = data.get("results") or []
    if not results:
        raise WeatherLookupError(
            f"Could not find a location matching {location!r}. Try a city "
            f"name (optionally with state/country), e.g. \"Austin, TX\"."
        )

    result = results[0]
    name_parts = [result.get("name")]
    if result.get("admin1"):
        name_parts.append(result["admin1"])
    if result.get("country"):
        name_parts.append(result["country"])

    return {
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "display_name": ", ".join(p for p in name_parts if p),
    }


def get_current(latitude: float, longitude: float) -> dict:
    """
    Get current conditions for a coordinate pair.

    Returns:
        A dict with temperature_f, feels_like_f, humidity_percent, wind_mph,
        precipitation_in, conditions, and as_of (ISO timestamp, local to the
        location).
    """
    data = _request(
        _FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
        },
    )

    current = data.get("current")
    if not current:
        raise WeatherLookupError("Open-Meteo returned no current-conditions data.")

    return {
        "temperature_f": current.get("temperature_2m"),
        "feels_like_f": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_mph": current.get("wind_speed_10m"),
        "precipitation_in": current.get("precipitation"),
        "conditions": _describe_weather_code(current.get("weather_code")),
        "as_of": current.get("time"),
    }


def get_daily_forecast(latitude: float, longitude: float, days: int) -> list[dict]:
    """
    Get a multi-day daily forecast for a coordinate pair.

    Args:
        days: Number of days to forecast (clamped to Open-Meteo's 1-16 range).

    Returns:
        A list of dicts (one per day, soonest first), each with date,
        temp_high_f, temp_low_f, precipitation_probability_percent,
        precipitation_in, and conditions.
    """
    days = max(1, min(days, 16))

    data = _request(
        _FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum,weather_code",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "forecast_days": days,
        },
    )

    daily = data.get("daily")
    if not daily:
        raise WeatherLookupError("Open-Meteo returned no daily-forecast data.")

    return [
        {
            "date": date,
            "temp_high_f": daily["temperature_2m_max"][i],
            "temp_low_f": daily["temperature_2m_min"][i],
            "precipitation_probability_percent": daily["precipitation_probability_max"][i],
            "precipitation_in": daily["precipitation_sum"][i],
            "conditions": _describe_weather_code(daily["weather_code"][i]),
        }
        for i, date in enumerate(daily["time"])
    ]


def parse_date(date_str: str) -> str:
    """Normalize a user-supplied date string to Open-Meteo's YYYY-MM-DD format."""
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except ValueError as e:
        raise WeatherLookupError(
            f"Could not parse date {date_str!r}; expected YYYY-MM-DD."
        ) from e
