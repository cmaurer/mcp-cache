"""
mcp_cache — reusable SQLite-backed cache for MCP servers.

Two strategies:
  - TTL cache    : cache any JSON-serializable value for N seconds
  - Time series  : store date-keyed observations and fetch only missing ranges
"""

from .cache import MCPCache

__all__ = ["MCPCache"]
