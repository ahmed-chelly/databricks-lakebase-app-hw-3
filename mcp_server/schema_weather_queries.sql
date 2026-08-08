-- Query-log schema for the weather MCP server's tool calls.
-- Run this SQL against your Lakebase Postgres database to create the table.
-- Populated by weather_mcp_server.py's _log_query() helper (best-effort,
-- one row per tool call) and read by dashboard/app.py's /api/recent.

CREATE TABLE IF NOT EXISTS weather_queries (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    location VARCHAR(255) NOT NULL,
    params JSONB,
    result_summary TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_weather_queries_created_at ON weather_queries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_weather_queries_email ON weather_queries(email);
