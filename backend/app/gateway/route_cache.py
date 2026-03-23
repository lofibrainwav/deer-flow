"""Redis-backed static route cache for Gateway config/metadata lookups.

Caches GET responses for models, skills, mcp, agents endpoints.
TTL 300s. Cache is invalidated on PUT/POST/DELETE to the same prefix.
"""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from functools import wraps

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("ROUTE_CACHE_REDIS_URL", "redis://127.0.0.1:6380/3")
CACHE_TTL = int(os.getenv("ROUTE_CACHE_TTL", "300"))
CACHE_PREFIX = "deerflow:route:"

_redis: aioredis.Redis | None = None
_stats = {"hits": 0, "misses": 0, "invalidations": 0, "errors": 0}


async def get_redis() -> aioredis.Redis | None:
    """Get or create async Redis connection."""
    global _redis
    if _redis is None:
        try:
            _redis = aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await _redis.ping()
            logger.info("Route cache connected to Redis: %s", REDIS_URL)
        except Exception as e:
            logger.warning("Route cache Redis unavailable: %s (cache disabled)", e)
            _redis = None
    return _redis


async def close_redis() -> None:
    """Close Redis connection."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def _cache_key(path: str, params: str = "") -> str:
    """Generate cache key from path and query params."""
    raw = f"{path}|{params}"
    return CACHE_PREFIX + hashlib.md5(raw.encode()).hexdigest()


async def cache_get(path: str, params: str = "") -> dict | None:
    """Get cached response. Returns None on miss or error."""
    r = await get_redis()
    if not r:
        return None
    try:
        key = _cache_key(path, params)
        data = await r.get(key)
        if data:
            _stats["hits"] += 1
            return json.loads(data)
        _stats["misses"] += 1
        return None
    except Exception as e:
        _stats["errors"] += 1
        logger.debug("Cache get error: %s", e)
        return None


async def cache_set(path: str, response_data: dict, params: str = "") -> None:
    """Store response in cache with TTL."""
    r = await get_redis()
    if not r:
        return
    try:
        key = _cache_key(path, params)
        await r.setex(key, CACHE_TTL, json.dumps(response_data))
    except Exception as e:
        _stats["errors"] += 1
        logger.debug("Cache set error: %s", e)


async def cache_invalidate(prefix: str) -> int:
    """Invalidate all cached entries matching a path prefix."""
    r = await get_redis()
    if not r:
        return 0
    try:
        # Since we hash keys, we use a tag pattern instead
        # Invalidate by scanning for our prefix
        count = 0
        async for key in r.scan_iter(match=f"{CACHE_PREFIX}*"):
            await r.delete(key)
            count += 1
        _stats["invalidations"] += count
        return count
    except Exception as e:
        _stats["errors"] += 1
        logger.debug("Cache invalidate error: %s", e)
        return 0


def get_cache_stats() -> dict:
    """Return cache hit/miss/error stats."""
    total = _stats["hits"] + _stats["misses"]
    hit_rate = (_stats["hits"] / total * 100) if total > 0 else 0
    return {
        **_stats,
        "total_requests": total,
        "hit_rate_pct": round(hit_rate, 1),
        "ttl_seconds": CACHE_TTL,
        "redis_url": REDIS_URL,
    }


def cached_route(path_pattern: str):
    """Decorator for caching GET route responses.

    Usage:
        @cached_route("/api/models")
        async def list_models() -> ModelsListResponse:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key from path pattern and kwargs
            params = json.dumps(kwargs, sort_keys=True) if kwargs else ""
            cached = await cache_get(path_pattern, params)
            if cached is not None:
                return cached

            # Cache miss — call original
            result = await func(*args, **kwargs)

            # Store in cache (convert pydantic models to dict)
            try:
                if hasattr(result, "model_dump"):
                    cache_data = result.model_dump()
                elif isinstance(result, dict):
                    cache_data = result
                else:
                    cache_data = result
                await cache_set(path_pattern, cache_data, params)
            except Exception:
                pass  # Don't fail the request on cache errors

            return result
        return wrapper
    return decorator
