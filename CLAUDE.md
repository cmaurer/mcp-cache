# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mcp-cache` is a reusable, zero-dependency (stdlib-only) SQLite-backed cache library for MCP (Model Context Protocol) servers. It ships as both an importable library (`from mcp_cache import MCPCache`) and a standalone MCP server (`mcp-cache` console script) for use directly in Claude Desktop. Requires Python 3.10+.

## Commands

```bash
pip install -e ".[dev]"                      # install package + test deps
pytest                                        # run full test suite
pytest tests/test_cache.py -v                 # verbose
pytest tests/test_cache.py -k test_name -v    # run a single test
pytest tests/test_cache.py -k list_keys -v    # run tests matching a substring
```

There is no linter/formatter configured in this repo — don't add one unprompted.

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (`pyproject.toml`), so async test functions need **no** `@pytest.mark.asyncio` decorator.

## Architecture

The library is two files under `src/mcp_cache/`:

- **`cache.py`** — `MCPCache`, the core SQLite-backed cache. No MCP/network dependency; pure stdlib (`sqlite3`, `asyncio`, `json`).
- **`server.py`** — a `FastMCP` server (`app = FastMCP("mcp-cache")`) that instantiates one module-level `MCPCache` and wraps each of its methods as an `@app.tool()`. This is what `pip install`'s `mcp-cache` console script runs.

New cache capability should almost always be added to `cache.py` first, then optionally exposed as a tool in `server.py` — not the other way around.

### Two caching strategies, one consistent pattern

`MCPCache` implements two independent strategies against one SQLite file, each with parallel method families:

- **TTL cache** (`ttl_cache` table) — arbitrary string keys → JSON blob + expiry. Methods: `get_or_fetch`, `get`, `set`, `invalidate`, `clear_expired`, `list_keys`.
- **Time series cache** (`ts_observations` + `ts_ranges` tables) — date-keyed observations per `series_id`, with gap-tracking so repeated queries only fetch missing date ranges. Methods: `get_timeseries`, `invalidate_series`, `list_series`.

Every public method follows the same shape: an `async def` method does `await asyncio.to_thread(self._sync_helper, ...)`, and a private sync helper (`_ttl_*` / `_ts_*` prefix) does the actual SQLite work via `self._connect()`. All SQLite connections go through `self._connect()`, which applies `busy_timeout`. Follow this pattern exactly when adding methods — don't call SQLite directly from an `async def`.

Any `LIKE`-based prefix filtering (`list_keys`, `list_series`) must go through the shared `_like_prefix()` helper (bottom of `cache.py`, escapes `%`/`_`/`\`) and bind the pattern as a parameter — never string-interpolate SQL.

### Time series gap detection

`get_timeseries` is the most complex piece: `_ts_find_gaps` merges overlapping/adjacent cached ranges from `ts_ranges` and computes the subset of `[start_date, end_date]` not yet covered, then only calls `fetch_fn` for those gaps before merging and returning the full requested range (sorted newest-first). When touching this logic, read `_ts_find_gaps` and its date-arithmetic helpers (`_prev_day`/`_next_day`) together — the gap math is easy to get off-by-one on.

### The `_MISSING` sentinel

TTL cache reads distinguish "no cached value" from "cached value is `None`" using the module-level `_MISSING` sentinel object (not `None`) as the miss marker internally. `get()` translates `_MISSING` back to `None` for the public API. Preserve this distinction in any code touching `_ttl_get`/`get_or_fetch`.

### Configuration

`server.py` reads three env vars at import time to configure the module-level cache instance: `MCP_CACHE_DB` (db file path, default `~/.cache/mcp_cache.db`), `MCP_CACHE_DEFAULT_TTL` (default `300`), `MCP_CACHE_BUSY_TIMEOUT` (default `5000` ms).

### Testing conventions

- Tests live in `tests/test_cache.py` only (no test file for `server.py`).
- Use the `tmp_path` fixture for a fresh SQLite file per test — never share a db across tests.
- To simulate expired/stale entries, insert rows directly via `sqlite3.connect(cache._db_path)` rather than sleeping.
- Use `unittest.mock.AsyncMock` for `fetch_fn`/`fetch` callables; assert with `.assert_awaited_once()` / `.await_count`.
- Tests hit a real SQLite file end-to-end — don't mock `MCPCache` internals.

### Release process

Versioning and `CHANGELOG.md` are fully automated by `python-semantic-release` on every push to `main` (`.github/workflows/release.yml`), driven by Conventional Commit prefixes (`feat:`, `fix:`, etc.) in commit messages. Do not hand-edit `CHANGELOG.md` or bump the version in `pyproject.toml` manually.
