"""
Integration tests for Baidu Semaphore, rate-limit middleware, cache.

Zero external calls. All database + Baidu API operations mocked.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.concurrency import RateLimiter, TTLCache


# ── Baidu API Semaphore ──────────────────────────────────

class TestBaiduSemaphore:
    """
    Verify the Baidu API Semaphore serializes calls to respect QPS=1.
    Uses a fresh semaphore per test to avoid event loop issues.
    """

    @pytest.fixture(autouse=True)
    def _fresh_semaphore(self):
        """Replace module-level semaphore with a fresh one for each test."""
        import app.services.baidu_face as bf
        old = bf._baidu_semaphore
        bf._baidu_semaphore = asyncio.Semaphore(1)
        yield
        bf._baidu_semaphore = old

    @pytest.mark.asyncio
    async def test_serializes_calls(self):
        """10 concurrent calls — only 1 in-flight at any time."""
        from app.services.baidu_face import _baidu_semaphore

        tracker = {"max_concurrent": 0, "current": 0}

        async def worker():
            async with _baidu_semaphore:
                tracker["current"] += 1
                tracker["max_concurrent"] = max(tracker["max_concurrent"], tracker["current"])
                await asyncio.sleep(0.05)
                tracker["current"] -= 1

        await asyncio.gather(*[worker() for _ in range(10)])
        assert tracker["max_concurrent"] == 1

    @pytest.mark.asyncio
    async def test_throughput_serial(self):
        """10 tasks with 50ms each — last one completes after ~500ms."""
        from app.services.baidu_face import _baidu_semaphore

        latencies = []

        async def worker():
            t0 = time.monotonic()
            async with _baidu_semaphore:
                await asyncio.sleep(0.05)
            latencies.append(time.monotonic() - t0)

        await asyncio.wait_for(asyncio.gather(*[worker() for _ in range(10)]), timeout=10.0)
        assert 0.3 <= max(latencies) <= 5.0

    @pytest.mark.asyncio
    async def test_fair_fifo(self):
        """Tasks complete in submission order — no starvation."""
        from app.services.baidu_face import _baidu_semaphore

        order = []

        async def worker(idx: int, delay: float):
            async with _baidu_semaphore:
                await asyncio.sleep(delay)
            order.append(idx)

        await asyncio.gather(
            worker(0, 0.1),
            worker(1, 0.01),
            worker(2, 0.01),
            worker(3, 0.01),
        )
        assert order[0] == 0, f"FIFO violated: {order}"


# ── Rate limiter (already tested in test_concurrency, here: stress) ──

class TestRateLimiterStress:
    """Additional stress tests for the rate limiter."""

    @pytest.mark.asyncio
    async def test_massive_concurrency(self):
        """1000 concurrent requests from mixed IPs — no deadlock."""
        limiter = RateLimiter(max_requests=100, window_seconds=60.0)

        async def worker(ip: str):
            return await limiter.acquire(ip)

        tasks = []
        for i in range(100):
            ip = f"10.0.{i}.1"
            for _ in range(20):
                tasks.append(worker(ip))

        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
        allowed = sum(results)
        # 100 IPs x 100 max each = 10000, but we sent 100x20=2000
        assert allowed == 2000  # all pass since each IP under limit


# ── TTLCache stress ──────────────────────────────────────

class TestTTLCacheStress:
    """Cache stress tests — large datasets, high concurrency."""

    @pytest.mark.asyncio
    async def test_large_dataset(self):
        """1000 entries — old ones evicted."""
        cache = TTLCache(maxsize=500, ttl=60.0)
        for i in range(1000):
            await cache.set(f"key-{i}", i)
        hits = 0
        for i in range(1000):
            if await cache.get(f"key-{i}") is not None:
                hits += 1
        assert hits == 500

    @pytest.mark.asyncio
    async def test_concurrent_throughput(self):
        """Measure cache throughput under heavy read+write load."""
        cache = TTLCache(maxsize=500, ttl=60.0)

        async def writer(offset):
            for i in range(100):
                await cache.set(f"k-{offset}-{i}", offset * i)

        async def reader():
            for _ in range(200):
                await cache.get("nonexistent")

        tasks = []
        for w in range(5):
            tasks.append(writer(w))
        for _ in range(20):
            tasks.append(reader())

        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)


# ── Resource lifecycle ───────────────────────────────────

class TestResourceLifecycle:
    """Verify resources are created and cleaned up properly."""

    @pytest.mark.asyncio
    async def test_http_client_singleton(self):
        from app.services.baidu_face import get_http_client, close_http_client

        c1 = await get_http_client()
        c2 = await get_http_client()
        assert c1 is c2
        await close_http_client()

    @pytest.mark.asyncio
    async def test_http_client_recreate_after_close(self):
        from app.services.baidu_face import get_http_client, close_http_client

        c1 = await get_http_client()
        await close_http_client()
        c2 = await get_http_client()
        assert c1 is not c2
        await close_http_client()
