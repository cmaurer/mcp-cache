# List Cache Keys — Design

## Problem

`MCPCache` has no way to enumerate what's currently cached. Callers can `get`/`set` individual keys or `get_timeseries` individual series, but there's no introspection method to list what keys/series exist — useful for debugging, cache management tooling, and the MCP server's diagnostic surface (alongside `stats()`).

## Approach

Add two separate listing methods, one per existing store, following the codebase's existing convention of splitting TTL-cache methods from time-series methods (`get`/`set`/`invalidate`/`clear_expired` vs. `get_timeseries`/`invalidate_series`). A single overloaded method with a `store` selector was considered but rejected — it would need different parameters per store (`include_expired` only makes sense for TTL) and existing code doesn't unify these stores elsewhere.

## API

```python
async def list_keys(self, prefix: str | None = None, include_expired: bool = False) -> list[str]:
    """Return TTL cache keys, optionally filtered by prefix. Fresh only unless include_expired=True."""

async def list_series(self, prefix: str | None = None) -> list[str]:
    """Return distinct time series series_ids, optionally filtered by prefix."""
```

- `list_keys` queries `ttl_cache`, excluding expired rows (`(now - cached_at) > ttl`) unless `include_expired=True`.
- `list_series` queries `SELECT DISTINCT series_id FROM ts_observations`.
- Both accept `prefix`: when set, filter with a bound `LIKE 'prefix%'` parameter (plain prefix match, no glob/regex). `prefix=None` (or omitted) returns everything.
- Both follow the existing sync-helper + `asyncio.to_thread` pattern used throughout `cache.py` (e.g. `_ttl_get`, `_ts_query`).
- Results are returned sorted alphabetically for deterministic output.

## Edge cases

- Empty cache/store → return `[]`, not an error.
- `prefix=""` is treated the same as `prefix=None` (no filtering) since `LIKE '%'` matches everything anyway — no special-casing needed.
- Prefix is passed as a bound SQL parameter, not string-interpolated — no injection risk.

## MCP server tool exposure

Add two tools in `server.py`, mirroring the existing `cache_stats` tool:

```python
@app.tool()
async def cache_list_keys(prefix: str = "", include_expired: bool = False) -> str:
    """List TTL cache keys, optionally filtered by prefix."""
    keys = await _cache.list_keys(prefix or None, include_expired)
    return json.dumps(keys)

@app.tool()
async def timeseries_list_series(prefix: str = "") -> str:
    """List time series IDs, optionally filtered by prefix."""
    series = await _cache.list_series(prefix or None)
    return json.dumps(series)
```

## Testing

Add to `tests/test_cache.py`, following existing conventions (real SQLite via `MCPCache`, `pytest.mark.asyncio`):

- `list_keys` returns `[]` on empty cache
- `list_keys` returns all fresh keys, alphabetically sorted
- `list_keys` excludes expired keys by default
- `list_keys(include_expired=True)` includes expired keys
- `list_keys(prefix=...)` filters correctly (matching and non-matching cases)
- `list_series` returns `[]` when no series cached
- `list_series` returns distinct series_ids (dedup across multiple date ranges for the same series)
- `list_series(prefix=...)` filters correctly

No test file exists for `server.py` currently, so no server-level tests are added — consistent with existing coverage.

## Out of scope

- Glob/regex pattern matching (prefix-only, per YAGNI)
- Pagination (no evidence of large enough key counts to need it)
- Combined "list everything" convenience method (caller can call both if needed)
