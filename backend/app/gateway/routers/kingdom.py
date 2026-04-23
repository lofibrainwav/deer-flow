"""Kingdom proxy — EROS score + system metrics via shared Redis (:6380) and /proc."""

import asyncio
import logging
import os
import time
from pathlib import Path

import redis.asyncio as redis
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kingdom", tags=["kingdom"])

# ── EROS ─────────────────────────────────────────────────────────────────────
# Kingdom writes EROS data to Redis db0 (default), not db3 (route cache).
# Create a dedicated connection to db0 for reading EROS data.


# Module-level Redis connection — reused across requests (avoids per-request connect overhead)
_eros_redis: redis.Redis | None = None


async def _get_eros_redis() -> redis.Redis | None:
    """Get (or create) Redis connection to db0 — singleton, not per-request."""
    global _eros_redis
    if _eros_redis is None:
        try:
            redis_url = os.getenv("KINGDOM_REDIS_URL", "redis://127.0.0.1:6380/0")
            _eros_redis = redis.from_url(redis_url, decode_responses=False)
            await _eros_redis.ping()
            logger.info("EROS Redis connected: %s", redis_url)
        except Exception as e:
            logger.warning("Could not connect to Kingdom Redis (db0): %s", e)
            return None
    return _eros_redis


@router.get("/eros", summary="Get EROS Score", description="Read EROS score (6 pillars + s_score) from Kingdom Redis.")
async def get_eros():
    """Read EROS score from Redis key `kingdom:eros:score`."""
    redis = await _get_eros_redis()
    if not redis:
        return {"success": False, "error": "Redis unavailable"}

    try:
        data = await redis.hgetall("kingdom:eros:score")
        if not data:
            return {"success": False, "error": "EROS data not found"}

        def float_or(val, default=0.0):
            try:
                if isinstance(val, bytes):
                    val = val.decode()
                return float(val)
            except (TypeError, ValueError):
                return default

        def str_or(val, default=""):
            if isinstance(val, bytes):
                val = val.decode()
            return val if val else default

        return {
            "success": True,
            "data": {
                "s_score": float_or(data.get(b"s_score")),
                "decision": str_or(data.get(b"decision")),
                "phase": str_or(data.get(b"phase")),
                "benevolence": float_or(data.get(b"benevolence")),
                "truth": float_or(data.get(b"truth")),
                "goodness": float_or(data.get(b"goodness")),
                "beauty": float_or(data.get(b"beauty")),
                "filial_piety": float_or(data.get(b"filial_piety")),
                "eternity": float_or(data.get(b"eternity")),
                "timestamp": str_or(data.get(b"timestamp")),
            },
        }
    except Exception as e:
        logger.error("Failed to read EROS from Redis: %s", e)
        return {"success": False, "error": str(e)}


# ── System Metrics ────────────────────────────────────────────────────────────

_cpu_prev: tuple[int, ...] | None = None
_cpu_prev_idle: int | None = None


def _read_cpu_percent() -> float:
    """Read CPU % from /proc/stat (Linux container-compatible)."""
    global _cpu_prev, _cpu_prev_idle
    try:
        stat = Path("/proc/stat").read_text().splitlines()[0]
        vals = [int(x) for x in stat.split()[1:]]
        total = sum(vals)
        idle = vals[3]
        if _cpu_prev is not None and _cpu_prev_idle is not None:
            total_delta = total - sum(_cpu_prev)
            idle_delta = idle - _cpu_prev_idle
            if total_delta > 0:
                pct = (1 - idle_delta / total_delta) * 100
                _cpu_prev = tuple(vals)
                _cpu_prev_idle = idle
                return round(pct, 1)
        _cpu_prev = tuple(vals)
        _cpu_prev_idle = idle
        return 0.0
    except Exception:
        return 0.0


def _read_mem_percent() -> float:
    """Read memory % from /proc/meminfo (Linux container-compatible)."""
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        mem = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0]] = int(parts[1]) * 1024
        total = mem.get("MemTotal:", 1)
        free = mem.get("MemFree:", 0)
        buffers = mem.get("Buffers:", 0)
        cached = mem.get("Cached:", 0)
        used = total - free - buffers - cached
        return round(max(used / total, 0) * 100, 1)
    except Exception:
        return 0.0


async def _ping_latency_ms() -> float:
    """Measure round-trip to Docker gateway itself as a latency proxy."""
    try:
        from app.gateway.config import get_gateway_config
        cfg = get_gateway_config()
        start = time.monotonic()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cfg.host, cfg.port),
            timeout=1.0,
        )
        writer.close()
        await writer.wait_closed()
        return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return 0.5


@router.get(
    "/system/metrics",
    summary="Get System Metrics",
    description="CPU, memory, latency — reads /proc inside the Docker container.",
)
async def get_system_metrics():
    """Return CPU/memory/latency from /proc and loopback ping."""
    try:
        cpu = _read_cpu_percent()
        mem = _read_mem_percent()
        latency = await _ping_latency_ms()

        return {
            "success": True,
            "data": {
                "cpuUsage": cpu,
                "memoryUsage": mem,
                "networkLatency": latency,
                "totalMemory": 0,
                "usedMemory": 0,
                "freeMemory": 0,
                "cpuCores": os.cpu_count() or 1,
                "loadAverage": [],
                "uptime": time.time(),
                "hostname": os.uname().nodename,
                "platform": os.uname().sysname,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
    except Exception as e:
        logger.error("Failed to read system metrics: %s", e)
        return {"success": False, "error": str(e)}
