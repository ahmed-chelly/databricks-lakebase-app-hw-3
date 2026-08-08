"""
Weather-prediction MCP server.

Exposes weather tools over MCP (Model Context Protocol) so a Databricks
Agent Bricks agent can call them like any other tool:
    - get_current_weather(location)
    - get_forecast(location, days)
    - predict_umbrella_needed(location, date)

Backed by Open-Meteo (see open_meteo_client.py) - a free, unauthenticated
weather API, so no Databricks secret is required for weather data itself.
Every tool call is best-effort logged to a Lakebase `weather_queries` table
(see lakebase.py / schema_weather_queries.sql) so the dashboard app can show
recent agent queries/predictions - a failed log write never breaks the
weather answer itself.

Deploy this as its own Databricks App (see app.yaml), separate from the
dashboard app, so an Agent Bricks agent (or any MCP client) can register its
URL as an external MCP server (see
https://docs.databricks.com/aws/en/agents/mcp-tools/custom-mcp).

Run locally:
    python weather_mcp_server.py
"""

import json
import logging
import os
from contextvars import ContextVar

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import lakebase
import open_meteo_client
from open_meteo_client import WeatherLookupError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

# An umbrella is recommended once forecasted precipitation probability
# exceeds this threshold - the "reasoning" behind predict_umbrella_needed.
UMBRELLA_THRESHOLD_PERCENT = 40

# Context variable to store request headers for accessing end-user identity
_request_context: ContextVar[dict] = ContextVar('request_context', default={})


def _get_end_user_email() -> str:
    """Get the actual end user's email from request headers, or fallback to service principal."""
    headers = _request_context.get()
    forwarded_user = headers.get('x-forwarded-user')
    if forwarded_user:
        return forwarded_user

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    return w.current_user.me().user_name or 'unknown'


mcp = FastMCP("weather-prediction")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Middleware to capture HTTP headers containing end-user identity."""
    async def dispatch(self, request: Request, call_next):
        headers = {
            'x-forwarded-user': request.headers.get('x-forwarded-user'),
            'x-forwarded-email': request.headers.get('x-forwarded-email'),
        }
        _request_context.set(headers)
        return await call_next(request)


def _log_query(tool_name: str, location: str, params: dict, result_summary: str) -> None:
    """Best-effort log of a tool call to Lakebase; never let a logging failure break a weather answer."""
    try:
        lakebase.run_write(
            """
            INSERT INTO weather_queries (email, tool_name, location, params, result_summary)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (_get_end_user_email(), tool_name, location, json.dumps(params), result_summary),
        )
    except Exception:
        logger.exception(f"Failed to log {tool_name} query for {location!r}")


@mcp.tool
def get_current_weather(location: str) -> dict:
    """
    Get current weather conditions for a location.

    Args:
        location: City name, optionally with state/country (e.g. "Austin, TX",
            "Chicago", "Paris, France"), or "latitude,longitude".

    Returns:
        On success: a dict with location, latitude, longitude, temperature_f,
        feels_like_f, humidity_percent, wind_mph, precipitation_in,
        conditions, and as_of.
        On failure: {"status": "error", "message": ...} - e.g. an
        unrecognized location or an Open-Meteo outage.
    """
    try:
        place = open_meteo_client.resolve_location(location)
        current = open_meteo_client.get_current(place["latitude"], place["longitude"])
        result = {
            "location": place["display_name"],
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            **current,
        }
        _log_query(
            "get_current_weather", location, {},
            f"{result['conditions']}, {result['temperature_f']}F",
        )
        return result
    except WeatherLookupError as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_forecast(location: str, days: int = 5) -> list[dict] | dict:
    """
    Get a multi-day weather forecast for a location.

    Args:
        location: City name (optionally with state/country) or "latitude,longitude".
        days: Number of days to forecast, 1-16 (default 5).

    Returns:
        On success: a list of dicts, one per day (soonest first), each with
        date, temp_high_f, temp_low_f, precipitation_probability_percent,
        precipitation_in, and conditions.
        On failure: {"status": "error", "message": ...} - e.g. an
        unrecognized location or an Open-Meteo outage.
    """
    try:
        place = open_meteo_client.resolve_location(location)
        forecast = open_meteo_client.get_daily_forecast(place["latitude"], place["longitude"], days)
        _log_query(
            "get_forecast", location, {"days": days},
            f"{len(forecast)}-day forecast for {place['display_name']}",
        )
        return forecast
    except WeatherLookupError as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def predict_umbrella_needed(location: str, date: str | None = None) -> dict:
    """
    Decide whether an umbrella is worth bringing for a location and date.

    This is a derived judgment call, not a passthrough of raw forecast data:
    an umbrella is recommended when the forecasted precipitation probability
    for that day exceeds UMBRELLA_THRESHOLD_PERCENT (40%).

    Args:
        location: City name (optionally with state/country) or "latitude,longitude".
        date: ISO date (YYYY-MM-DD) to check, within the next 16 days.
            Defaults to today (the location's local today) if omitted.

    Returns:
        On success: a dict with location, date, precipitation_probability_percent,
        conditions, umbrella_recommended (bool), and reasoning (str).
        On failure: {"status": "error", "message": ...} - e.g. an
        unrecognized location, a date outside the 16-day forecast window,
        or an Open-Meteo outage.
    """
    try:
        place = open_meteo_client.resolve_location(location)
        forecast = open_meteo_client.get_daily_forecast(place["latitude"], place["longitude"], 16)

        target_date = open_meteo_client.parse_date(date) if date else forecast[0]["date"]
        day = next((d for d in forecast if d["date"] == target_date), None)
        if day is None:
            return {
                "status": "error",
                "message": (
                    f"{target_date} is outside the available 16-day forecast "
                    f"({forecast[0]['date']} to {forecast[-1]['date']})."
                ),
            }

        probability = day["precipitation_probability_percent"]
        recommended = probability is not None and probability > UMBRELLA_THRESHOLD_PERCENT
        reasoning = (
            f"Precipitation probability is {probability}%, which is "
            f"{'above' if recommended else 'at or below'} the "
            f"{UMBRELLA_THRESHOLD_PERCENT}% threshold, so an umbrella is "
            f"{'recommended' if recommended else 'not needed'}."
        )

        result = {
            "location": place["display_name"],
            "date": target_date,
            "precipitation_probability_percent": probability,
            "conditions": day["conditions"],
            "umbrella_recommended": recommended,
            "reasoning": reasoning,
        }
        _log_query(
            "predict_umbrella_needed", location, {"date": target_date},
            f"umbrella_recommended={recommended} ({probability}%)",
        )
        return result
    except WeatherLookupError as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # Add middleware to capture request headers for end-user identity
    # This must be done before mcp.run() is called
    if hasattr(mcp, 'app') and mcp.app is not None:
        mcp.app.add_middleware(RequestContextMiddleware)

    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # streamable-http is the transport Databricks' MCP client/gateway expects
    # (see the "Host your own MCP" doc linked in the module docstring above).
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)
