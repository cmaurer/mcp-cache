# List Cache Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the ability to list what's currently cached — TTL cache keys and time series IDs — with optional prefix filtering.

**Architecture:** Two new async methods on `MCPCache` (`list_keys`, `list_series`), each following the existing sync-helper + `asyncio.to_thread` pattern already used by every other method in `cache.py`. Two new MCP tools in `server.py` expose them to Claude Desktop, mirroring the existing tool style. README gets matching API reference and tool table entries.

**Tech Stack:** Python 3.10+, sqlite3 (stdlib), pytest + pytest-asyncio (`asyncio_mode = "auto"`, no decorator needed).

## Global Constraints

- Follow the existing `cache.py` pattern: a public `async def` method that does `await asyncio.to_thread(self._sync_helper, ...)`, plus a private sync helper that opens a connection via `self._connect()`.
- `prefix` filtering uses a bound SQL parameter (`LIKE ? `), never string interpolation.
- `list_keys` excludes expired entries by default; `include_expired=True` includes them.
- Both methods return results sorted alphabetically.
- Tests go in the existing `tests/test_cache.py`, using the same style as neighboring tests (`tmp_path` fixture, direct `sqlite3.connect` for pre-seeding rows, no custom fixtures/classes).

---

### Task 1: `MCPCache.list_keys()` — TTL cache key listing

**Files:**
- Modify: `src/mcp_cache/cache.py` (add method under the `# --- TTL cache ---` section, after `clear_expired`/`_ttl_clear_expired`, i.e. after line 153)
- Test: `tests/test_cache.py` (add tests under the `# ── TTL cache` section, after `test_upsert_updates_on_re_store`, i.e. after line 167)

**Interfaces:**
- Produces: `async def list_keys(self, prefix: str | None = None, include_expired: bool = False) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache.py` after `test_upsert_updates_on_re_store` (line 167):

```python
async def test_list_keys_empty_cache(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    assert await cache.list_keys() == []


async def test_list_keys_returns_fresh_keys_sorted(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.set("zebra", "v")
    await cache.set("apple", "v")
    assert await cache.list_keys() == ["apple", "zebra"]


async def test_list_keys_excludes_expired_by_default(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    old = time.time() - 9999
    with sqlite3.connect(cache._db_path) as conn:
        conn.executemany(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            [
                ("stale", '"a"', old, 300),
                ("fresh", '"b"', time.time(), 3600),
            ],
        )
    assert await cache.list_keys() == ["fresh"]


async def test_list_keys_include_expired_true(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    old = time.time() - 9999
    with sqlite3.connect(cache._db_path) as conn:
        conn.executemany(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            [
                ("stale", '"a"', old, 300),
                ("fresh", '"b"', time.time(), 3600),
            ],
        )
    assert await cache.list_keys(include_expired=True) == ["fresh", "stale"]


async def test_list_keys_prefix_filter(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.set("fred:T10YIE", "v")
    await cache.set("fred:DGS10", "v")
    await cache.set("other:key", "v")
    assert await cache.list_keys(prefix="fred:") == ["fred:DGS10", "fred:T10YIE"]


async def test_list_keys_prefix_no_match(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.set("fred:T10YIE", "v")
    assert await cache.list_keys(prefix="nomatch:") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py -k list_keys -v`
Expected: FAIL with `AttributeError: 'MCPCache' object has no attribute 'list_keys'`

- [ ] **Step 3: Implement `list_keys` and `_ttl_list_keys`**

In `src/mcp_cache/cache.py`, add after `_ttl_clear_expired` (after line 153, before the `# --- Time series cache ---` comment on line 155):

```python
    async def list_keys(self, prefix: str | None = None, include_expired: bool = False) -> list[str]:
        """Return TTL cache keys, optionally filtered by prefix.

        Excludes expired entries unless include_expired=True.
        """
        return await asyncio.to_thread(self._ttl_list_keys, prefix, include_expired)

    def _ttl_list_keys(self, prefix: str | None, include_expired: bool) -> list[str]:
        query = "SELECT key FROM ttl_cache"
        conditions = []
        params: list[Any] = []
        if not include_expired:
            conditions.append("(? - cached_at) <= ttl")
            params.insert(0, time.time())
        if prefix:
            conditions.append("key LIKE ? ESCAPE '\\'")
            params.append(_like_prefix(prefix))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY key"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row[0] for row in rows]
```

Add the `_like_prefix` helper near the other module-level helpers (`_prev_day`/`_next_day` at the bottom of the file, after line 288):

```python
def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -k list_keys -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_cache/cache.py tests/test_cache.py
git commit -m "feat: add MCPCache.list_keys() to list TTL cache keys"
```

---

### Task 2: `MCPCache.list_series()` — time series ID listing

**Files:**
- Modify: `src/mcp_cache/cache.py` (add method under `# --- Time series cache ---`, after `_ts_delete_series`, i.e. after what will now be line ~275 once Task 1's addition shifts line numbers — insert directly after the `_ts_delete_series` method and before the `# --- Diagnostics ---` comment)
- Test: `tests/test_cache.py` (add tests under the `# ── time series` section, after `test_timeseries_empty_fetch_result`)

**Interfaces:**
- Consumes: none from Task 1 (independent method, same file)
- Produces: `async def list_series(self, prefix: str | None = None) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache.py` after `test_timeseries_empty_fetch_result` (originally line 281, before the `# ── constructor parameters` section):

```python
async def test_list_series_empty_cache(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    assert await cache.list_series() == []


async def test_list_series_returns_distinct_sorted(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01"]))
    f2 = AsyncMock(return_value=_obs(["2024-01-01"]))
    await cache.get_timeseries("zebra", "2024-01-01", "2024-01-01", f1)
    await cache.get_timeseries("apple", "2024-01-01", "2024-01-01", f2)
    assert await cache.list_series() == ["apple", "zebra"]


async def test_list_series_dedups_across_ranges(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01"]))
    f2 = AsyncMock(return_value=_obs(["2024-02-01"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-01", f1)
    await cache.get_timeseries("S1", "2024-02-01", "2024-02-01", f2)
    assert await cache.list_series() == ["S1"]


async def test_list_series_prefix_filter(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01"]))
    f2 = AsyncMock(return_value=_obs(["2024-01-01"]))
    await cache.get_timeseries("fred:T10YIE", "2024-01-01", "2024-01-01", f1)
    await cache.get_timeseries("other:S1", "2024-01-01", "2024-01-01", f2)
    assert await cache.list_series(prefix="fred:") == ["fred:T10YIE"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py -k list_series -v`
Expected: FAIL with `AttributeError: 'MCPCache' object has no attribute 'list_series'`

- [ ] **Step 3: Implement `list_series` and `_ts_list_series`**

In `src/mcp_cache/cache.py`, add after `_ts_delete_series` (right before the `# --- Diagnostics ---` comment):

```python
    async def list_series(self, prefix: str | None = None) -> list[str]:
        """Return distinct time series IDs, optionally filtered by prefix."""
        return await asyncio.to_thread(self._ts_list_series, prefix)

    def _ts_list_series(self, prefix: str | None) -> list[str]:
        query = "SELECT DISTINCT series_id FROM ts_observations"
        params: list[Any] = []
        if prefix:
            query += " WHERE series_id LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(prefix))
        query += " ORDER BY series_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row[0] for row in rows]
```

(`_like_prefix` was already added in Task 1 — reuse it, don't redefine it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -k list_series -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_cache/cache.py tests/test_cache.py
git commit -m "feat: add MCPCache.list_series() to list time series IDs"
```

---

### Task 3: Expose MCP tools and update README

**Files:**
- Modify: `src/mcp_cache/server.py` (add two `@app.tool()` functions after `cache_clear_expired`, i.e. after line 48, and after `timeseries_invalidate`, i.e. after line 95)
- Modify: `README.md` (add API reference entries and tool table rows)

**Interfaces:**
- Consumes: `MCPCache.list_keys(prefix, include_expired)` from Task 1, `MCPCache.list_series(prefix)` from Task 2
- Produces: MCP tools `cache_list_keys`, `timeseries_list_series` (no other task depends on these)

- [ ] **Step 1: Add `cache_list_keys` tool**

In `src/mcp_cache/server.py`, add after `cache_clear_expired` (after line 48, before the `timeseries_store` tool):

```python
@app.tool()
async def cache_list_keys(prefix: str = "", include_expired: bool = False) -> str:
    """List TTL cache keys, optionally filtered by prefix."""
    keys = await _cache.list_keys(prefix or None, include_expired)
    return json.dumps(keys)
```

- [ ] **Step 2: Add `timeseries_list_series` tool**

In `src/mcp_cache/server.py`, add after `timeseries_invalidate` (after line 95, before the `cache_stats` tool):

```python
@app.tool()
async def timeseries_list_series(prefix: str = "") -> str:
    """List time series IDs, optionally filtered by prefix."""
    series = await _cache.list_series(prefix or None)
    return json.dumps(series)
```

- [ ] **Step 3: Verify the server imports and tools register**

Run: `python -c "from mcp_cache.server import app; print(sorted(t for t in __import__('asyncio').run(app.list_tools())))" 2>&1 | tail -5`

If that's awkward to run directly, instead run:

Run: `python -c "import mcp_cache.server"`
Expected: no import errors (exit code 0)

- [ ] **Step 4: Update README API reference**

In `README.md`, add after the `#### await cache.clear_expired()` section (after line 99, before the `---` on line 101):

```markdown

#### `await cache.list_keys(prefix=None, include_expired=False)`

Return TTL cache keys as a sorted list of strings.

- `prefix` — only return keys starting with this string; `None` (default) returns all keys
- `include_expired` — include expired entries; defaults to `False` (fresh keys only)
```

Add after the `#### await cache.invalidate_series(series_id)` section (after line 118, before the `---` on line 120):

```markdown

#### `await cache.list_series(prefix=None)`

Return distinct time series IDs as a sorted list of strings.

- `prefix` — only return series IDs starting with this string; `None` (default) returns all series
```

- [ ] **Step 5: Update README MCP tool table**

In `README.md`, update the tool table (around line 176-184) to add two rows — after `cache_clear_expired()` and after `timeseries_invalidate(series_id)`:

```markdown
| `cache_get(key)` | Return a cached value, or `null` if missing/expired |
| `cache_set(key, value, ttl)` | Store a JSON value for `ttl` seconds (default 300) |
| `cache_invalidate(key)` | Remove a specific entry |
| `cache_clear_expired()` | Delete all expired entries |
| `cache_list_keys(prefix, include_expired)` | List TTL cache keys, optionally filtered by prefix |
| `timeseries_store(series_id, observations, range_start, range_end)` | Store date-keyed observations |
| `timeseries_get(series_id, start_date, end_date)` | Query cached observations |
| `timeseries_invalidate(series_id)` | Clear all data for a series |
| `timeseries_list_series(prefix)` | List time series IDs, optionally filtered by prefix |
| `cache_stats()` | Return entry counts and db path |
```

This replaces the existing 8-row table with the 10-row version above (same rows, two new ones inserted in place).

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/mcp_cache/server.py README.md
git commit -m "feat: expose cache_list_keys and timeseries_list_series MCP tools"
```

---

## Self-Review Notes

- **Spec coverage:** `list_keys` (Task 1) ✓, `list_series` (Task 2) ✓, prefix filtering both (Tasks 1 & 2) ✓, include_expired (Task 1) ✓, MCP tool exposure (Task 3) ✓, tests per spec's testing plan (Tasks 1 & 2) ✓. Spec's "out of scope" items (glob matching, pagination, combined method) are correctly omitted.
- **Type consistency:** `list_keys(prefix: str | None, include_expired: bool) -> list[str]` and `list_series(prefix: str | None) -> list[str]` match between the spec, task interfaces, and implementation code in every task.
- **Shared helper:** `_like_prefix` is defined once in Task 1 and reused (not redefined) in Task 2 — flagged explicitly in Task 2 to avoid a duplicate-definition mistake.
