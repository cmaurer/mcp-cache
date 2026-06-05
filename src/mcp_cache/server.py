"""Standalone MCP server exposing MCPCache as tools for Claude Desktop."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .cache import MCPCache

_DEFAULT_DB = Path.home() / ".cache" / "mcp_cache.db"
_db_path = Path(os.environ.get("MCP_CACHE_DB", _DEFAULT_DB)).expanduser()
_default_ttl = int(os.environ.get("MCP_CACHE_DEFAULT_TTL", 300))
_busy_timeout = int(os.environ.get("MCP_CACHE_BUSY_TIMEOUT", 5000))

app = FastMCP("mcp-cache")
_cache = MCPCache(_db_path, default_ttl=_default_ttl, busy_timeout=_busy_timeout)


@app.tool()
async def cache_get(key: str) -> str:
    """Return the cached JSON value for key, or null if missing or expired."""
    value = await _cache.get(key)
    return json.dumps(value)


@app.tool()
async def cache_set(key: str, value: str, ttl: int = 300) -> str:
    """Store value (a JSON string) under key for ttl seconds."""
    await _cache.set(key, json.loads(value), ttl)
    return f"cached '{key}' for {ttl}s"


@app.tool()
async def cache_invalidate(key: str) -> str:
    """Remove a specific TTL cache entry."""
    await _cache.invalidate(key)
    return f"invalidated '{key}'"


@app.tool()
async def cache_clear_expired() -> str:
    """Delete all expired TTL entries and return the count removed."""
    count = await _cache.clear_expired()
    return f"removed {count} expired entries"


@app.tool()
async def timeseries_store(
    series_id: str,
    observations: str,
    range_start: str,
    range_end: str,
    date_key: str = "date",
    value_key: str = "value",
) -> str:
    """Store time series observations.

    observations — JSON array of objects each containing date_key and value_key.
    range_start / range_end — YYYY-MM-DD boundaries that were fetched (used for gap tracking).
    """
    obs = json.loads(observations)
    await asyncio.to_thread(
        _cache._ts_store, series_id, obs, range_start, range_end, date_key, value_key
    )
    return f"stored {len(obs)} observations for '{series_id}' [{range_start} – {range_end}]"


@app.tool()
async def timeseries_get(
    series_id: str,
    start_date: str,
    end_date: str,
    date_key: str = "date",
    value_key: str = "value",
) -> str:
    """Return cached observations for series_id between start_date and end_date (YYYY-MM-DD).

    Only returns already-cached data — does not call any external fetch.
    Results are sorted newest-first.
    """
    rows = await asyncio.to_thread(
        _cache._ts_query, series_id, start_date, end_date, date_key, value_key
    )
    return json.dumps(rows)


@app.tool()
async def timeseries_invalidate(series_id: str) -> str:
    """Remove all cached observations and range records for series_id."""
    await _cache.invalidate_series(series_id)
    return f"invalidated series '{series_id}'"


@app.tool()
async def cache_stats() -> str:
    """Return cache statistics (entry counts, series counts, db path) as JSON."""
    return json.dumps(await _cache.stats(), indent=2)


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
