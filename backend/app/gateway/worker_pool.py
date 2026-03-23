"""LangGraph persistent worker pool with pre-warming and adaptive scaling.

Pre-warms httpx connections to LangGraph server to eliminate cold-start
overhead under load. Connections are kept alive and reused across requests.

Config: pre_warm=4, min_workers=2, max_workers=8
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://127.0.0.1:2024")
PRE_WARM = int(os.getenv("WORKER_POOL_PRE_WARM", "4"))
MIN_WORKERS = int(os.getenv("WORKER_POOL_MIN", "2"))
MAX_WORKERS = int(os.getenv("WORKER_POOL_MAX", "8"))
HEALTH_INTERVAL = 30  # seconds between health checks


@dataclass
class PoolStats:
    """Pool runtime statistics."""

    total_requests: int = 0
    active_requests: int = 0
    queue_full_count: int = 0
    peak_active: int = 0
    last_health_check: float = 0
    pool_size: int = 0


@dataclass
class WorkerPool:
    """Persistent connection pool to LangGraph server."""

    _clients: list[httpx.AsyncClient] = field(default_factory=list)
    _semaphore: asyncio.Semaphore | None = None
    _stats: PoolStats = field(default_factory=PoolStats)
    _started: bool = False

    async def start(self) -> None:
        """Initialize pool with pre-warmed connections."""
        if self._started:
            return

        self._semaphore = asyncio.Semaphore(MAX_WORKERS)

        # Create pre-warmed clients
        for i in range(PRE_WARM):
            client = httpx.AsyncClient(
                base_url=LANGGRAPH_URL,
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=10.0),
                limits=httpx.Limits(
                    max_keepalive_connections=MAX_WORKERS,
                    max_connections=MAX_WORKERS,
                    keepalive_expiry=300,
                ),
                http2=False,  # LangGraph dev doesn't support h2
            )
            # Warm the connection with a health check
            try:
                resp = await client.get("/ok")
                if resp.status_code == 200:
                    logger.info("Worker %d pre-warmed successfully", i)
                else:
                    logger.warning("Worker %d pre-warm got status %d", i, resp.status_code)
            except Exception as e:
                logger.warning("Worker %d pre-warm failed: %s", i, e)

            self._clients.append(client)

        self._stats.pool_size = len(self._clients)
        self._started = True
        logger.info(
            "WorkerPool started: pre_warm=%d, min=%d, max=%d, url=%s",
            PRE_WARM, MIN_WORKERS, MAX_WORKERS, LANGGRAPH_URL,
        )

    async def stop(self) -> None:
        """Gracefully close all connections."""
        for client in self._clients:
            try:
                await client.aclose()
            except Exception:
                pass
        self._clients.clear()
        self._started = False
        logger.info("WorkerPool stopped")

    def _get_client(self) -> httpx.AsyncClient:
        """Round-robin client selection."""
        if not self._clients:
            raise RuntimeError("WorkerPool not started")
        idx = self._stats.total_requests % len(self._clients)
        return self._clients[idx]

    async def request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        headers: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        """Execute a request through the pool with concurrency control."""
        if not self._semaphore:
            raise RuntimeError("WorkerPool not started")

        acquired = self._semaphore._value > 0  # noqa: SLF001
        if not acquired:
            self._stats.queue_full_count += 1
            logger.warning("Worker pool saturated (active=%d, max=%d)", self._stats.active_requests, MAX_WORKERS)

        async with self._semaphore:
            self._stats.total_requests += 1
            self._stats.active_requests += 1
            if self._stats.active_requests > self._stats.peak_active:
                self._stats.peak_active = self._stats.active_requests

            try:
                client = self._get_client()
                response = await client.request(
                    method,
                    path,
                    content=content,
                    headers=headers,
                    params=params,
                )
                return response
            finally:
                self._stats.active_requests -= 1

    async def health_check(self) -> dict:
        """Run health check and return pool stats."""
        now = time.time()
        self._stats.last_health_check = now

        # Check LangGraph availability
        lg_ok = False
        try:
            client = self._get_client()
            resp = await client.get("/ok", timeout=3.0)
            lg_ok = resp.status_code == 200
        except Exception:
            pass

        return {
            "pool_size": self._stats.pool_size,
            "active_requests": self._stats.active_requests,
            "total_requests": self._stats.total_requests,
            "peak_active": self._stats.peak_active,
            "queue_full_count": self._stats.queue_full_count,
            "langgraph_reachable": lg_ok,
            "config": {
                "pre_warm": PRE_WARM,
                "min_workers": MIN_WORKERS,
                "max_workers": MAX_WORKERS,
            },
        }

    @property
    def stats(self) -> PoolStats:
        return self._stats


# Singleton
_pool: WorkerPool | None = None


def get_pool() -> WorkerPool:
    """Get the singleton worker pool instance."""
    global _pool
    if _pool is None:
        _pool = WorkerPool()
    return _pool
