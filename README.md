# Weather-Prediction MCP Server + Agent

Built on the Day 3 pattern (`databricks-lakebase-app-day-3`: Agent Bricks + an external MCP
server, deployed as a Databricks App), but for weather instead of paper trading:

- A **weather MCP server** (`mcp_server/`) - exposes weather tools (`get_current_weather`,
  `get_forecast`, `predict_umbrella_needed`) over the Model Context Protocol, backed by
  [Open-Meteo](https://open-meteo.com/), a free, unauthenticated weather API - no API key,
  no signup, no Databricks secret needed for weather data itself.
- A **Databricks Agent Bricks agent** that connects to that MCP server as an external tool
  and answers natural-language weather questions ("Will it rain in Chicago tomorrow?",
  "Should I bring an umbrella to Austin this weekend?").
- A small **dashboard app** (`dashboard/`) showing recent weather questions/predictions the
  agent has made, read from a Lakebase log table the MCP server writes to.

> **Why Open-Meteo?** It needs zero credentials to call, so the entire pipeline (adapter →
> MCP tools → agent) can be built and tested before worrying about secrets management at all.
> The only secret this project still uses is the Lakebase connection URL, and only because the
> dashboard logs/reads recent agent queries there - the weather tools themselves never touch it.

## Architecture

```
Agent Bricks agent  --(MCP tool calls)-->  mcp_server/weather_mcp_server.py  --(REST)-->  Open-Meteo (free, no key)
                                                        |
                                                        | (best-effort log: weather_queries table)
                                                        v
                                                    Lakebase
                                                        ^
                                                        | (reads recent queries)
                                        dashboard/app.py
```

- `mcp_server/` and `dashboard/` are **two separate Databricks Apps** - one serves MCP tool
  calls to the agent, the other serves a human-facing dashboard. Both call the same
  Open-Meteo adapter (`open_meteo_client.py`, duplicated in each folder since Databricks Apps
  deploy independently with no shared-package install step).
- `mcp_server/open_meteo_client.py` is the adapter: all HTTP calls to Open-Meteo's geocoding
  and forecast APIs, plus WMO weather-code → text parsing, live here. The MCP tool functions
  never call `requests` directly.
- `mcp_server/weather_mcp_server.py` wraps the adapter with [FastMCP](https://gofastmcp.com/)
  `@mcp.tool` decorators and serves them over streamable HTTP - the transport Databricks' MCP
  client/gateway expects when you
  [host your own MCP server as a Databricks App](https://docs.databricks.com/aws/en/agents/mcp-tools/custom-mcp).
- Every tool call is logged (best-effort, never blocking the weather answer) to a
  `weather_queries` table in Lakebase, which `dashboard/app.py` reads to show recent agent
  activity.

## Tools

| Tool | Purpose |
|---|---|
| `get_current_weather(location)` | Current temperature, feels-like, humidity, wind, precipitation, and conditions. |
| `get_forecast(location, days=5)` | Daily forecast (1-16 days): highs/lows, precipitation probability, conditions. |
| `predict_umbrella_needed(location, date=None)` | Derived recommendation: umbrella is recommended when forecasted precipitation probability for that day exceeds 40% - see `UMBRELLA_THRESHOLD_PERCENT` in `weather_mcp_server.py`. Returns a `reasoning` string, not just the raw number. |

`location` accepts a city name (optionally with state/country, e.g. `"Austin, TX"`) or a
`"latitude,longitude"` pair. An unrecognized location or an Open-Meteo outage returns
`{"status": "error", "message": ...}` from every tool - never a stack trace.

## Files

- `mcp_server/weather_mcp_server.py` - FastMCP server exposing the 3 weather tools
- `mcp_server/open_meteo_client.py` - Adapter wrapping Open-Meteo's geocoding + forecast APIs
- `mcp_server/lakebase.py` - Lakebase (Postgres) connection helper, used only for query logging
- `mcp_server/schema_weather_queries.sql` - Creates the `weather_queries` log table
- `mcp_server/test_weather_client.py` - Local smoke test for the adapter (no Databricks needed)
- `mcp_server/app.yaml` / `mcp_server/requirements.txt` - Databricks App config for the MCP server
- `dashboard/app.py` - Flask dashboard: recent agent queries + a manual lookup form
- `dashboard/templates/index.html` - Dashboard UI
- `dashboard/open_meteo_client.py` / `dashboard/lakebase.py` - copies of the same adapter/helper
  (each Databricks App deploys from its own folder, so each needs its own copy)
- `dashboard/app.yaml` / `dashboard/requirements.txt` - Databricks App config for the dashboard
- `setup_secrets.py` - One-time script to store the Lakebase URL secret
- `.env.example` - Local dev env var template

## Step-by-step setup

### 1. Create (or reuse) a Lakebase instance

Only needed for the dashboard's query log - see
[Day 2's step 2](../databricks-lakebase-app-day-2/README.md#2-create-a-lakebase-instance-and-a-native-password-role)
if you don't already have one.

### 2. Store the Lakebase secret

From a Databricks notebook (`%sh python setup_secrets.py`) or the CLI:

```bash
databricks secrets create-scope database
databricks secrets put-secret database lakebase-url --string-value "$(echo -n 'postgresql://role:password@host:5432/databricks_postgres?sslmode=require' | base64)"
```

### 3. Create the query-log table

Run `mcp_server/schema_weather_queries.sql` against your Lakebase database (e.g. via a SQL
client connected with the same role/URL, or a Databricks notebook `%sql` cell).

### 4. Configure environment variables (local dev)

```bash
cp .env.example .env
# paste your Lakebase URL into LAKEBASE_URL
```

### 5. Install dependencies and run both apps locally

```bash
cd mcp_server && pip install -r requirements.txt && python test_weather_client.py   # sanity check, no secrets needed
python weather_mcp_server.py   # serves MCP on :8000
```

In a second terminal:

```bash
cd dashboard && pip install -r requirements.txt && python app.py   # serves UI on :8001
```

Open `http://localhost:8001` to see the dashboard (empty query log until the agent - or the
manual lookup form - runs a query). Use an
[MCP Inspector](https://docs.databricks.com/aws/en/agents/mcp-tools/connect-clients) or `curl`
against `http://localhost:8000` to sanity-check the tools before deploying.

### 6. Deploy both apps to Databricks Apps

Following [Day 2's step 7](../databricks-lakebase-app-day-2/README.md#7-create-a-git-folder-in-databricks-and-deploy-the-app-no-cli-required)
(Git folder + Apps UI, no CLI needed), but this time deploy **two** apps pointed at two
different subfolders of the same Git folder:

1. Create a Git folder for this repo (once).
2. **Deploy the MCP server app**: Compute > Apps > Create app > Custom, name it e.g.
   `weather-mcp`, and point its source at the Git folder's `mcp_server/` subfolder (so it
   picks up `mcp_server/app.yaml`). Deploy it, then copy its app URL - you'll register that
   URL as an external MCP server in step 7.
3. **Deploy the dashboard app**: repeat, naming it e.g. `weather-dashboard`, pointing at
   `dashboard/`. Deploy it and open its URL to confirm the dashboard loads.

### 7. Register the MCP server as an external MCP in your workspace

Follow [Connect agents to external MCPs and tools](https://docs.databricks.com/aws/en/agents/mcp-tools/connect-external):

1. In your workspace, go to **AI Gateway** > **MCPs** > **Add MCP** (or **Register external MCP**).
2. Paste the `weather-mcp` app's URL from step 6 as the server endpoint (streamable HTTP).
3. Give it a name (e.g. `weather-prediction`) and save. Databricks will introspect the server
   and list the 3 tools (`get_current_weather`, `get_forecast`, `predict_umbrella_needed`).
4. Grant your Agent Bricks agent (created next) access to this MCP server via Unity Catalog
   permissions, if prompted.

### 8. Build the Agent Bricks agent

1. In your workspace sidebar, go to **Agents** > **Agent Bricks** > **Create agent**.
2. Choose the **Custom LLM** agent type (a single tool-calling agent is enough here).
3. Under **Tools**, add the `weather-prediction` MCP server you registered in step 7 (all 3 tools).
4. Give the agent this system prompt:

   > You are a weather assistant. Always resolve the user's location and call a tool to get
   > real data before answering - never guess temperatures, forecasts, or precipitation
   > chances. Use `get_current_weather` for "what's it like right now" questions,
   > `get_forecast` for multi-day questions, and `predict_umbrella_needed` for any question
   > about whether to bring an umbrella/jacket or whether an outdoor plan is a good idea. If a
   > tool returns a `status: error` (e.g. an unrecognized location or an API outage), tell the
   > user and ask them to clarify rather than fabricating an answer.

5. **Evaluate and iterate**: use Agent Bricks' auto-evaluation against sample prompts (e.g.
   "Will it rain in Chicago tomorrow?") to tune the system prompt and tool selection.
6. Deploy the agent and chat with it, e.g.:
   - *"What's the weather like right now in Seattle?"*
   - *"Give me a 5-day forecast for Denver."*
   - *"Should I bring a jacket to Austin this weekend?"*

   Watch each query show up in the dashboard's recent-queries table (from step 6) as the
   agent calls the MCP tools.

### 9. Demonstrate the agent working

Paste or screenshot at least 3 different natural-language questions and the agent's
tool-calling + final answers (this is required for submission).

## Notes

- `mcp_server/` and `dashboard/` intentionally duplicate `open_meteo_client.py` and
  `lakebase.py` rather than sharing a package, because each Databricks App deploys
  independently from its own folder with its own `app.yaml`/`requirements.txt` - there's no
  shared Python package install step across Databricks Apps.
- No secrets are committed to git and no API key is hardcoded - Open-Meteo requires none, and
  the only secret used (`database/lakebase-url`) is fetched at runtime via
  `WorkspaceClient().secrets.get_secret()` (see `lakebase.py`), never stored in code.
