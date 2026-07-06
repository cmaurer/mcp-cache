# CHANGELOG


## v0.2.2 (2026-07-06)

### Bug Fixes

- Wheel builds were shipping empty packages ([#3](https://github.com/cmaurer/mcp-cache/pull/3),
  [`c35f270`](https://github.com/cmaurer/mcp-cache/commit/c35f270f6e43a592ecfd04017af5ee6a1f58dbe9))

python -m build builds the sdist first, then builds the wheel from that sdist. The top-level
  [tool.hatch.build] sources = ["src"] rewrite strips the src/ prefix when creating the sdist, so by
  the time the wheel target's packages = ["src/mcp_cache"] is evaluated against the sdist's layout,
  that path no longer exists — producing a wheel with only dist-info and no package files. Every
  published version (0.1.0 through 0.2.1) is affected.

Removing the redundant top-level sources config fixes it — verified the resulting wheel contains
  __init__.py/cache.py/server.py and that MCPCache actually imports and works when installed from
  it.

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>


## v0.2.1 (2026-07-06)

### Bug Fixes

- Add cache key/series listing methods ([#2](https://github.com/cmaurer/mcp-cache/pull/2),
  [`140bc30`](https://github.com/cmaurer/mcp-cache/commit/140bc30044f78f6ba36a9ac359234cd0fbb345d6))

* docs: add design spec for cache key/series listing methods

* docs: add implementation plan for cache key/series listing

* feat: add MCPCache.list_keys() to list TTL cache keys

Implement list_keys() method with optional prefix filtering and expired entry handling. Includes
  _ttl_list_keys() helper and _like_prefix() escaping utility.

* feat: add MCPCache.list_series() to list time series IDs

Implement list_series() method with optional prefix filtering: - Public async method using
  asyncio.to_thread pattern - Private sync helper _ts_list_series with SQL LIKE filtering - Reuses
  _like_prefix helper from Task 1 - Results sorted alphabetically - Added 4 comprehensive tests
  covering empty cache, deduplication, and prefix filtering

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

* feat: expose cache_list_keys and timeseries_list_series MCP tools

* docs: add CLAUDE.md with architecture and command reference

---------

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>


## v0.2.0 (2026-06-05)

### Features

- Make MCP server configuration configurable via env vars
  ([#1](https://github.com/cmaurer/mcp-cache/pull/1),
  [`5e757b5`](https://github.com/cmaurer/mcp-cache/commit/5e757b51897a83662f94ae5980b80d21cd26f795))

* feat: make SQLite db path configurable via MCP_CACHE_DB env var

Defaults to ~/.cache/mcp_cache.db (existing behavior unchanged). Set MCP_CACHE_DB to any path,
  including a Google Drive mount, in the Claude Desktop env config to relocate the database.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>

* feat: add MCP_CACHE_DEFAULT_TTL and MCP_CACHE_BUSY_TIMEOUT env vars

Exposes default_ttl and busy_timeout as environment-configurable settings. Introduces
  MCPCache._connect() to apply busy_timeout consistently across all SQLite connections.

* test: add coverage for default_ttl and busy_timeout constructor params

---------

Co-authored-by: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>


## v0.1.1 (2026-06-04)

### Bug Fixes

- Cache None values correctly in TTL cache
  ([`885b19f`](https://github.com/cmaurer/mcp-cache/commit/885b19f7667755d5c103381dc69713d592a75164))

get_or_fetch and get() both returned None for a missing key and for a legitimately cached None
  value, causing a false cache miss on the second call. Introduced a _MISSING sentinel so the two
  cases are distinguished.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>


## v0.1.0 (2026-06-04)

### Chores

- Adding missed files
  ([`85840c4`](https://github.com/cmaurer/mcp-cache/commit/85840c404426d3cbdbc1a86a2938d8f1ad6963ed))

### Continuous Integration

- Add PyPI publish workflow on GitHub release
  ([`954a8f3`](https://github.com/cmaurer/mcp-cache/commit/954a8f3f50fb2c8c288ce2a3d63d87092a75d993))

Uses OIDC trusted publishing — no API token secret required.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>

- Replace publish.yml with semantic-release workflow
  ([`6368f0f`](https://github.com/cmaurer/mcp-cache/commit/6368f0f2cd69531d6e79c86e70b34e5164150d98))

- release.yml: runs python-semantic-release on every push to main, bumps version in pyproject.toml,
  creates GitHub release, and publishes to PyPI (requires PYPI_API_TOKEN secret) - pyproject.toml:
  add [tool.semantic_release] config pointing at project.version for automated version bumps -
  remove publish.yml (release.yml now owns PyPI publishing)

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>

### Features

- Add MCP server for Claude Desktop
  ([`91f2fe6`](https://github.com/cmaurer/mcp-cache/commit/91f2fe6116762067f0d149a21a1eeddb3b42d5da))

- server.py: FastMCP server exposing 8 tools (cache_get/set/invalidate, cache_clear_expired,
  timeseries_store/get/invalidate, cache_stats) - MCPCache: add public get() and set() methods -
  pyproject.toml: mcp>=1.0 dependency + mcp-cache console script - README: Claude Desktop config and
  tool reference table - tests: 4 new tests for get/set public methods (33 total)

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>

- Add unit tests, README, and GitHub Actions CI
  ([`64857e1`](https://github.com/cmaurer/mcp-cache/commit/64857e1bad8b2af67f604cafae3eedc459214cc9))

- 29 pytest-asyncio tests covering TTL cache, time series gap detection, invalidation, diagnostics,
  and edge cases - README with installation, quick-start, and full API reference - CI workflow: test
  matrix across Python 3.10–3.13 + build artifact job - pyproject.toml: dev extras (pytest,
  pytest-asyncio) and pytest config

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>
