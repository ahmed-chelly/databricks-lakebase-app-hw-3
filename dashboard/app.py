"""
Weather dashboard: shows recent weather questions/predictions the Agent
Bricks agent has made through the weather MCP server
(mcp_server/weather_mcp_server.py), plus a manual lookup form for ad-hoc
queries against the same Open-Meteo adapter.

Reads the weather_queries table in Lakebase (populated by
weather_mcp_server.py's _log_query() helper) via lakebase.py - this app
never calls the MCP server directly, it only reads the log the agent leaves
behind.

Deploy this as its OWN Databricks App (separate from weather_mcp_server.py) -
one app serves MCP tool calls, the other serves the human-facing UI.

Run locally:
    python app.py
"""

import os

from flask import Flask, jsonify, render_template, request

import lakebase
import open_meteo_client
from open_meteo_client import WeatherLookupError

app = Flask(__name__)

DEFAULT_RECENT_LIMIT = int(os.environ.get("RECENT_QUERIES_LIMIT", 25))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page)."""
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Dashboard UI: recent agent queries + a manual lookup form."""
    return render_template("index.html")


@app.route("/api/recent")
def api_recent():
    """Recent weather queries/predictions logged by the agent, most recent first."""
    limit = int(request.args.get("limit", DEFAULT_RECENT_LIMIT))
    rows = lakebase.run_query(
        """
        SELECT email, tool_name, location, params, result_summary, created_at
        FROM weather_queries
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return jsonify(rows)


@app.route("/api/weather")
def api_weather():
    """Ad-hoc weather lookup, for manually checking a location in the UI."""
    location = request.args.get("location", "")
    if not location:
        return jsonify({"error": "location query param is required"}), 400
    try:
        place = open_meteo_client.resolve_location(location)
        current = open_meteo_client.get_current(place["latitude"], place["longitude"])
        return jsonify({"location": place["display_name"], **current})
    except WeatherLookupError as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8001))
    app.run(debug=True, host=host, port=port)
