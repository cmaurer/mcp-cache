"""SQLite-backed cache with TTL and time series strategies."""

import asyncio
import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable


_MISSING = object()  # sentinel distinguishing a cached None from a cache miss


def _to_date_str(d: date | str) -> str:
    if isinstance(d, str):
        return d
    return d.isoformat()


class MCPCache:
    """
    Persistent SQLite cache for MCP server API responses.

    Usage — TTL cache (any API response):
        cache = MCPCache("~/.cache/myserver.db")
        data = await cache.get_or_fetch("fred:T10YIE", fetch_fn, ttl=3600)

    Usage — time series (date-keyed observations):
        observations = await cache.get_timeseries(
            series_id="T10YIE",
            start_date="2020-01-01",
            end_date="2024-12-31",
            fetch_fn=lambda s, e: api.get_series("T10YIE", s, e),
        )
    """

    def __init__(
        self,
        db_path: str | Path = "mcp_cache.db",
        default_ttl: int = 300,
        busy_timeout: int = 5000,
    ):
        self._db_path = str(Path(db_path).expanduser().resolve())
        self._default_ttl = default_ttl
        self._busy_timeout = busy_timeout
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=self._busy_timeout / 1000)
        return conn

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ttl_cache (
                    key        TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    cached_at  REAL NOT NULL,
                    ttl        INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ts_observations (
                    series_id TEXT NOT NULL,
                    obs_date  TEXT NOT NULL,
                    value     REAL,
                    PRIMARY KEY (series_id, obs_date)
                );

                CREATE TABLE IF NOT EXISTS ts_ranges (
                    series_id  TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date   TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ts_ranges_series
                    ON ts_ranges (series_id, start_date, end_date);
            """)

    # --- TTL cache ---

    async def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Awaitable[Any]],
        ttl: int | None = None,
    ) -> Any:
        """Return cached value if fresh; otherwise call fetch_fn, cache, and return."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        cached = await asyncio.to_thread(self._ttl_get, key)
        if cached is not _MISSING:
            return cached
        value = await fetch_fn()
        await asyncio.to_thread(self._ttl_set, key, value, effective_ttl)
        return value

    def _ttl_get(self, key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json, cached_at, ttl FROM ttl_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return _MISSING
        value_json, cached_at, ttl = row
        if time.time() - cached_at > ttl:
            return _MISSING
        return json.loads(value_json)

    def _ttl_set(self, key: str, value: Any, ttl: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ttl_cache (key, value_json, cached_at, ttl)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    cached_at  = excluded.cached_at,
                    ttl        = excluded.ttl
                """,
                (key, json.dumps(value), time.time(), ttl),
            )

    async def get(self, key: str) -> Any | None:
        """Return the cached value for key, or None if missing or expired."""
        result = await asyncio.to_thread(self._ttl_get, key)
        return None if result is _MISSING else result

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store value under key. Uses default_ttl if ttl is not specified."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        await asyncio.to_thread(self._ttl_set, key, value, effective_ttl)

    async def invalidate(self, key: str) -> None:
        """Remove a specific TTL cache entry."""
        await asyncio.to_thread(self._ttl_delete, key)

    def _ttl_delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ttl_cache WHERE key = ?", (key,))

    async def clear_expired(self) -> int:
        """Delete all expired TTL entries. Returns the number removed."""
        return await asyncio.to_thread(self._ttl_clear_expired)

    def _ttl_clear_expired(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM ttl_cache WHERE (? - cached_at) > ttl", (time.time(),)
            )
            return cur.rowcount

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

    # --- Time series cache ---

    async def get_timeseries(
        self,
        series_id: str,
        start_date: date | str,
        end_date: date | str,
        fetch_fn: Callable[[str, str], Awaitable[list[dict]]],
        date_key: str = "date",
        value_key: str = "value",
    ) -> list[dict]:
        """
        Return observations for series_id in [start_date, end_date].

        Computes which sub-ranges are not yet cached, calls fetch_fn(start, end)
        for each gap (ISO date strings), stores new observations, and returns
        the merged result sorted descending by date (newest first).

        fetch_fn must return a list of dicts with date_key and value_key.
        Null/None values are stored — they represent real data points.
        """
        start = _to_date_str(start_date)
        end = _to_date_str(end_date)
        gaps = await asyncio.to_thread(self._ts_find_gaps, series_id, start, end)
        for gap_start, gap_end in gaps:
            new_obs = await fetch_fn(gap_start, gap_end)
            await asyncio.to_thread(
                self._ts_store, series_id, new_obs, gap_start, gap_end, date_key, value_key
            )
        return await asyncio.to_thread(self._ts_query, series_id, start, end, date_key, value_key)

    def _ts_find_gaps(self, series_id: str, start: str, end: str) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT start_date, end_date FROM ts_ranges
                WHERE series_id = ? AND start_date <= ? AND end_date >= ?
                ORDER BY start_date
                """,
                (series_id, end, start),
            ).fetchall()

        if not rows:
            return [(start, end)]

        merged: list[tuple[str, str]] = []
        for r_start, r_end in rows:
            if merged and r_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], r_end))
            else:
                merged.append((r_start, r_end))

        gaps: list[tuple[str, str]] = []
        cursor = start
        for covered_start, covered_end in merged:
            if cursor < covered_start:
                gaps.append((cursor, _prev_day(covered_start)))
            cursor = max(cursor, _next_day(covered_end))
        if cursor <= end:
            gaps.append((cursor, end))
        return gaps

    def _ts_store(self, series_id, observations, range_start, range_end, date_key, value_key):
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO ts_observations (series_id, obs_date, value)
                VALUES (?, ?, ?)
                ON CONFLICT(series_id, obs_date) DO UPDATE SET value = excluded.value
                """,
                [
                    (series_id, str(obs[date_key]), obs.get(value_key))
                    for obs in observations
                    if date_key in obs
                ],
            )
            conn.execute(
                "INSERT INTO ts_ranges (series_id, start_date, end_date, fetched_at) VALUES (?, ?, ?, ?)",
                (series_id, range_start, range_end, time.time()),
            )

    def _ts_query(self, series_id, start, end, date_key, value_key):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT obs_date, value FROM ts_observations
                WHERE series_id = ? AND obs_date >= ? AND obs_date <= ?
                ORDER BY obs_date DESC
                """,
                (series_id, start, end),
            ).fetchall()
        return [{date_key: row[0], value_key: row[1]} for row in rows]

    async def invalidate_series(self, series_id: str) -> None:
        """Remove all cached observations and range records for a series."""
        await asyncio.to_thread(self._ts_delete_series, series_id)

    def _ts_delete_series(self, series_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ts_observations WHERE series_id = ?", (series_id,))
            conn.execute("DELETE FROM ts_ranges WHERE series_id = ?", (series_id,))

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

    # --- Diagnostics ---

    async def stats(self) -> dict:
        """Return counts and basic info about the cache contents."""
        return await asyncio.to_thread(self._stats)

    def _stats(self) -> dict:
        now = time.time()
        with self._connect() as conn:
            ttl_total = conn.execute("SELECT COUNT(*) FROM ttl_cache").fetchone()[0]
            ttl_fresh = conn.execute(
                "SELECT COUNT(*) FROM ttl_cache WHERE (? - cached_at) <= ttl", (now,)
            ).fetchone()[0]
            ts_series = conn.execute(
                "SELECT COUNT(DISTINCT series_id) FROM ts_observations"
            ).fetchone()[0]
            ts_obs = conn.execute("SELECT COUNT(*) FROM ts_observations").fetchone()[0]
            ts_ranges = conn.execute("SELECT COUNT(*) FROM ts_ranges").fetchone()[0]
        return {
            "ttl_cache": {"total": ttl_total, "fresh": ttl_fresh, "expired": ttl_total - ttl_fresh},
            "timeseries": {"series": ts_series, "observations": ts_obs, "fetched_ranges": ts_ranges},
            "db_path": self._db_path,
        }


def _prev_day(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
