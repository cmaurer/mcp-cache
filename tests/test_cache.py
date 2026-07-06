"""Unit tests for MCPCache — TTL cache, time series, and diagnostics."""

import sqlite3
import time
from datetime import date
from unittest.mock import AsyncMock

import pytest

from mcp_cache import MCPCache
from mcp_cache.cache import _next_day, _prev_day, _to_date_str


# ── helper functions ────────────────────────────────────────────────────────


def test_to_date_str_passthrough():
    assert _to_date_str("2024-06-01") == "2024-06-01"


def test_to_date_str_from_date_object():
    assert _to_date_str(date(2024, 6, 1)) == "2024-06-01"


def test_prev_day_mid_month():
    assert _prev_day("2024-06-15") == "2024-06-14"


def test_prev_day_month_boundary():
    assert _prev_day("2024-06-01") == "2024-05-31"


def test_next_day_mid_month():
    assert _next_day("2024-06-15") == "2024-06-16"


def test_next_day_year_boundary():
    assert _next_day("2024-12-31") == "2025-01-01"


# ── TTL cache ───────────────────────────────────────────────────────────────


async def test_cache_miss_calls_fetch(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value={"v": 1})
    result = await cache.get_or_fetch("k", fetch)
    assert result == {"v": 1}
    fetch.assert_awaited_once()


async def test_cache_hit_skips_fetch(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value={"v": 1})
    await cache.get_or_fetch("k", fetch)
    result = await cache.get_or_fetch("k", fetch)
    assert result == {"v": 1}
    fetch.assert_awaited_once()


async def test_cache_expired_entry_refetches(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    # Insert a stale entry directly so we don't need to sleep
    with sqlite3.connect(cache._db_path) as conn:
        conn.execute(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            ("k", '"old"', time.time() - 9999, 300),
        )
    fetch = AsyncMock(return_value="fresh")
    result = await cache.get_or_fetch("k", fetch)
    assert result == "fresh"
    fetch.assert_awaited_once()


async def test_per_call_ttl_overrides_default(tmp_path):
    cache = MCPCache(tmp_path / "test.db", default_ttl=3600)
    # Insert an entry that is stale under ttl=1 but fresh under default 3600
    with sqlite3.connect(cache._db_path) as conn:
        conn.execute(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            ("k", '"old"', time.time() - 5, 1),
        )
    fetch = AsyncMock(return_value="new")
    result = await cache.get_or_fetch("k", fetch, ttl=1)
    assert result == "new"
    fetch.assert_awaited_once()


async def test_invalidate_removes_entry(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value="data")
    await cache.get_or_fetch("k", fetch)
    await cache.invalidate("k")
    await cache.get_or_fetch("k", fetch)
    assert fetch.await_count == 2


async def test_invalidate_nonexistent_key_is_noop(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.invalidate("no-such-key")  # must not raise


async def test_clear_expired_removes_stale_entries(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    old = time.time() - 9999
    with sqlite3.connect(cache._db_path) as conn:
        conn.executemany(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            [
                ("stale1", '"a"', old, 300),
                ("stale2", '"b"', old, 300),
                ("fresh", '"c"', time.time(), 3600),
            ],
        )
    removed = await cache.clear_expired()
    assert removed == 2


async def test_ttl_cache_stores_various_json_types(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    cases = [("s", "hello"), ("i", 42), ("f", 3.14), ("l", [1, 2]), ("d", {"a": 1}), ("b", True)]
    for key, value in cases:
        result = await cache.get_or_fetch(key, AsyncMock(return_value=value))
        assert result == value


async def test_cached_none_is_not_treated_as_miss(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=None)
    await cache.get_or_fetch("k", fetch)
    await cache.get_or_fetch("k", fetch)
    fetch.assert_awaited_once()  # second call must hit cache, not re-fetch


async def test_get_returns_none_on_miss(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    assert await cache.get("missing") is None


async def test_set_then_get_roundtrip(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.set("k", {"x": 1})
    assert await cache.get("k") == {"x": 1}


async def test_set_uses_default_ttl(tmp_path):
    cache = MCPCache(tmp_path / "test.db", default_ttl=3600)
    await cache.set("k", "v")
    with sqlite3.connect(cache._db_path) as conn:
        ttl = conn.execute("SELECT ttl FROM ttl_cache WHERE key = 'k'").fetchone()[0]
    assert ttl == 3600


async def test_set_explicit_ttl_overrides_default(tmp_path):
    cache = MCPCache(tmp_path / "test.db", default_ttl=3600)
    await cache.set("k", "v", ttl=60)
    with sqlite3.connect(cache._db_path) as conn:
        ttl = conn.execute("SELECT ttl FROM ttl_cache WHERE key = 'k'").fetchone()[0]
    assert ttl == 60


async def test_upsert_updates_on_re_store(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.get_or_fetch("k", AsyncMock(return_value="v1"))
    await cache.invalidate("k")
    result = await cache.get_or_fetch("k", AsyncMock(return_value="v2"))
    assert result == "v2"


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


# ── time series ─────────────────────────────────────────────────────────────


def _obs(dates, value=1.0):
    return [{"date": d, "value": value} for d in dates]


async def test_timeseries_full_miss_calls_fetch(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02", "2024-01-03"]))
    result = await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    fetch.assert_awaited_once_with("2024-01-01", "2024-01-03")
    assert len(result) == 3


async def test_timeseries_full_hit_skips_fetch(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02", "2024-01-03"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    fetch.assert_awaited_once()


async def test_timeseries_partial_gap_fetches_only_missing(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02", "2024-01-03"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", f1)

    f2 = AsyncMock(return_value=_obs(["2024-01-04", "2024-01-05"]))
    result = await cache.get_timeseries("S1", "2024-01-01", "2024-01-05", f2)
    f2.assert_awaited_once_with("2024-01-04", "2024-01-05")
    assert len(result) == 5


async def test_timeseries_gap_in_middle(tmp_path):
    """Cached [Jan1-3] and [Jan7-9]; requesting [Jan1-9] should only fetch [Jan4-6]."""
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02", "2024-01-03"]))
    f2 = AsyncMock(return_value=_obs(["2024-01-07", "2024-01-08", "2024-01-09"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", f1)
    await cache.get_timeseries("S1", "2024-01-07", "2024-01-09", f2)

    f3 = AsyncMock(return_value=_obs(["2024-01-04", "2024-01-05", "2024-01-06"]))
    result = await cache.get_timeseries("S1", "2024-01-01", "2024-01-09", f3)
    f3.assert_awaited_once_with("2024-01-04", "2024-01-06")
    assert len(result) == 9


async def test_timeseries_result_sorted_descending(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02", "2024-01-03"]))
    result = await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    dates = [r["date"] for r in result]
    assert dates == sorted(dates, reverse=True)


async def test_timeseries_accepts_date_objects(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=_obs(["2024-06-01"]))
    result = await cache.get_timeseries("S1", date(2024, 6, 1), date(2024, 6, 1), fetch)
    assert len(result) == 1


async def test_timeseries_custom_date_value_keys(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    obs = [{"t": "2024-01-01", "price": 99.5}]
    fetch = AsyncMock(return_value=obs)
    result = await cache.get_timeseries(
        "S1", "2024-01-01", "2024-01-01", fetch, date_key="t", value_key="price"
    )
    assert result == [{"t": "2024-01-01", "price": 99.5}]


async def test_timeseries_null_values_stored(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    obs = [{"date": "2024-01-01", "value": None}]
    fetch = AsyncMock(return_value=obs)
    result = await cache.get_timeseries("S1", "2024-01-01", "2024-01-01", fetch)
    assert result[0]["value"] is None


async def test_invalidate_series_clears_all_data(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-02", f1)
    await cache.invalidate_series("S1")

    f2 = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-02", f2)
    f2.assert_awaited_once()


async def test_invalidate_series_does_not_affect_other_series(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    f1 = AsyncMock(return_value=_obs(["2024-01-01"]))
    f2 = AsyncMock(return_value=_obs(["2024-01-01"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-01", f1)
    await cache.get_timeseries("S2", "2024-01-01", "2024-01-01", f2)
    await cache.invalidate_series("S1")

    f2_again = AsyncMock(return_value=_obs(["2024-01-01"]))
    await cache.get_timeseries("S2", "2024-01-01", "2024-01-01", f2_again)
    f2_again.assert_not_awaited()


async def test_timeseries_empty_fetch_result(tmp_path):
    """fetch_fn returning [] should store the range so it isn't re-fetched."""
    cache = MCPCache(tmp_path / "test.db")
    fetch = AsyncMock(return_value=[])
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-03", fetch)
    fetch.assert_awaited_once()


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


# ── constructor parameters ──────────────────────────────────────────────────


async def test_default_ttl_used_when_no_ttl_specified(tmp_path):
    cache = MCPCache(tmp_path / "test.db", default_ttl=999)
    await cache.set("k", "v")
    with sqlite3.connect(cache._db_path) as conn:
        ttl = conn.execute("SELECT ttl FROM ttl_cache WHERE key = 'k'").fetchone()[0]
    assert ttl == 999


async def test_busy_timeout_stored_on_instance(tmp_path):
    cache = MCPCache(tmp_path / "test.db", busy_timeout=12345)
    assert cache._busy_timeout == 12345


async def test_busy_timeout_raises_on_locked_db(tmp_path):
    db = tmp_path / "test.db"
    cache = MCPCache(db, busy_timeout=100)  # 100 ms — fails fast
    # Hold an exclusive lock from a second connection
    blocker = sqlite3.connect(str(db))
    blocker.execute("BEGIN EXCLUSIVE")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        cache._ttl_get("any-key")
    blocker.close()


async def test_default_busy_timeout_is_5000ms(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    assert cache._busy_timeout == 5000


# ── stats ───────────────────────────────────────────────────────────────────


async def test_stats_empty_cache(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    s = await cache.stats()
    assert s["ttl_cache"] == {"total": 0, "fresh": 0, "expired": 0}
    assert s["timeseries"] == {"series": 0, "observations": 0, "fetched_ranges": 0}
    assert s["db_path"] == cache._db_path


async def test_stats_reflects_cache_state(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    await cache.get_or_fetch("k1", AsyncMock(return_value=1))
    await cache.get_or_fetch("k2", AsyncMock(return_value=2))
    fetch = AsyncMock(return_value=_obs(["2024-01-01", "2024-01-02"]))
    await cache.get_timeseries("S1", "2024-01-01", "2024-01-02", fetch)

    s = await cache.stats()
    assert s["ttl_cache"]["total"] == 2
    assert s["ttl_cache"]["fresh"] == 2
    assert s["ttl_cache"]["expired"] == 0
    assert s["timeseries"]["observations"] == 2
    assert s["timeseries"]["series"] == 1
    assert s["timeseries"]["fetched_ranges"] == 1


async def test_stats_counts_expired_correctly(tmp_path):
    cache = MCPCache(tmp_path / "test.db")
    old = time.time() - 9999
    with sqlite3.connect(cache._db_path) as conn:
        conn.executemany(
            "INSERT INTO ttl_cache (key, value_json, cached_at, ttl) VALUES (?, ?, ?, ?)",
            [
                ("stale", '"x"', old, 300),
                ("fresh", '"y"', time.time(), 3600),
            ],
        )
    s = await cache.stats()
    assert s["ttl_cache"]["total"] == 2
    assert s["ttl_cache"]["fresh"] == 1
    assert s["ttl_cache"]["expired"] == 1
