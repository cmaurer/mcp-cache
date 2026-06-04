# mcp-cache

A reusable SQLite-backed cache for MCP (Model Context Protocol) servers. Zero dependencies — pure Python stdlib.

Two caching strategies:

- **TTL cache** — store any JSON-serializable value for N seconds
- **Time series cache** — store date-keyed observations and automatically fetch only missing date ranges

## Installation

```bash
pip install mcp-cache
```

Or from source:

```bash
git clone https://github.com/cmaurer/mcp-cache
cd mcp-cache
pip install -e .
```

Requires Python 3.10+.

## Quick start

### TTL cache

```python
from mcp_cache import MCPCache

cache = MCPCache("~/.cache/myserver.db", default_ttl=300)

async def get_quote(symbol: str) -> dict:
    return await cache.get_or_fetch(
        key=f"quote:{symbol}",
        fetch_fn=lambda: api.fetch_quote(symbol),
        ttl=60,  # override default; omit to use default_ttl
    )
```

`fetch_fn` is called only on a cache miss or after the TTL expires. The result is stored as JSON and returned on subsequent calls within the TTL window.

### Time series cache

```python
observations = await cache.get_timeseries(
    series_id="T10YIE",
    start_date="2020-01-01",
    end_date="2024-12-31",
    fetch_fn=lambda s, e: fred_api.get_series("T10YIE", s, e),
)
# Returns list of {"date": "YYYY-MM-DD", "value": float} dicts, newest first
```

On the first call the full range is fetched. On subsequent calls only gaps are fetched — requesting a wider range re-uses the already-cached portion and fetches only the missing edges.

Custom key names are supported for APIs that don't use `"date"` / `"value"`:

```python
observations = await cache.get_timeseries(
    series_id="prices",
    start_date="2024-01-01",
    end_date="2024-06-30",
    fetch_fn=my_fetch,
    date_key="timestamp",
    value_key="close",
)
```

## API reference

### `MCPCache(db_path, default_ttl)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | `str \| Path` | `"mcp_cache.db"` | Path to SQLite file. `~` is expanded. Parent dirs are created automatically. |
| `default_ttl` | `int` | `300` | Default TTL in seconds for `get_or_fetch`. |

---

### TTL cache methods

#### `await cache.get_or_fetch(key, fetch_fn, ttl=None)`

Return the cached value for `key` if it exists and is still fresh. Otherwise call `fetch_fn()`, store the result, and return it.

- `key` — cache key string
- `fetch_fn` — async callable that returns a JSON-serializable value
- `ttl` — per-call TTL override in seconds; uses `default_ttl` if omitted

#### `await cache.invalidate(key)`

Remove a specific entry from the TTL cache. No-op if the key does not exist.

#### `await cache.clear_expired()`

Delete all expired TTL entries. Returns the number of entries removed.

---

### Time series methods

#### `await cache.get_timeseries(series_id, start_date, end_date, fetch_fn, date_key="date", value_key="value")`

Return cached observations for `series_id` in `[start_date, end_date]`.

- `series_id` — identifier for the series
- `start_date`, `end_date` — `date` objects or ISO strings (`"YYYY-MM-DD"`)
- `fetch_fn(start, end)` — async callable receiving ISO date strings; must return a list of dicts containing `date_key` and optionally `value_key`
- `date_key`, `value_key` — key names in the returned dicts (default `"date"`, `"value"`)

Returns a list of `{date_key: str, value_key: float | None}` dicts sorted **newest first**. `None` values are preserved — they represent real data points (e.g. non-trading days).

#### `await cache.invalidate_series(series_id)`

Remove all cached observations and range records for a series.

---

### Diagnostics

#### `await cache.stats()`

Returns a dict with cache counts:

```python
{
    "ttl_cache":  {"total": 12, "fresh": 10, "expired": 2},
    "timeseries": {"series": 3, "observations": 1500, "fetched_ranges": 6},
    "db_path":    "/home/user/.cache/myserver.db",
}
```

## Using with an MCP server

```python
from mcp_cache import MCPCache

_cache = MCPCache("~/.cache/myserver.db")

@server.tool()
async def get_price_history(symbol: str, start: str, end: str) -> list[dict]:
    return await _cache.get_timeseries(
        series_id=symbol,
        start_date=start,
        end_date=end,
        fetch_fn=lambda s, e: data_provider.fetch(symbol, s, e),
    )
```

## Using with Claude Desktop

Install the package:

```bash
pip install mcp-cache
```

Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cache": {
      "command": "mcp-cache"
    }
  }
}
```

Restart Claude Desktop. The following tools will be available:

| Tool | Description |
|------|-------------|
| `cache_get(key)` | Return a cached value, or `null` if missing/expired |
| `cache_set(key, value, ttl)` | Store a JSON value for `ttl` seconds (default 300) |
| `cache_invalidate(key)` | Remove a specific entry |
| `cache_clear_expired()` | Delete all expired entries |
| `timeseries_store(series_id, observations, range_start, range_end)` | Store date-keyed observations |
| `timeseries_get(series_id, start_date, end_date)` | Query cached observations |
| `timeseries_invalidate(series_id)` | Clear all data for a series |
| `cache_stats()` | Return entry counts and db path |

The cache database is stored at `~/.cache/mcp_cache.db`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
