"""
Tests for concurrency primitives: RateLimiter, TTLCache, Baidu API Semaphore.

All tests run purely in-memory — no external API calls, zero cost.
"""

import asyncio
import time

import pytest

from app.concurrency import RateLimiter, TTLCache


# ── RateLimiter ──────────────────────────────────────────

class TestRateLimiter:
    """Sliding-window per-IP rate limiter."""

    @pytest.mark.asyncio
    async def test_allow_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            assert await limiter.acquire("192.168.1.1") is True

    @pytest.mark.asyncio
    async def test_deny_when_exceeded(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            assert await limiter.acquire("10.0.0.1") is True
        assert await limiter.acquire("10.0.0.1") is False

    @pytest.mark.asyncio
    async def test_different_ips_independent(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60.0)
        assert await limiter.acquire("ip-a") is True
        assert await limiter.acquire("ip-a") is True
        assert await limiter.acquire("ip-a") is False  # ip-a exhausted
        assert await limiter.acquire("ip-b") is True   # ip-b still has quota

    @pytest.mark.asyncio
    async def test_window_resets_after_expiry(self):
        limiter = RateLimiter(max_requests=2, window_seconds=0.3)
        assert await limiter.acquire("test") is True
        assert await limiter.acquire("test") is True
        assert await limiter.acquire("test") is False
        await asyncio.sleep(0.35)
        assert await limiter.acquire("test") is True  # window slid

    @pytest.mark.asyncio
    async def test_high_concurrency_same_ip(self):
        """300 concurrent requests from same IP — only max_requests allowed."""
        limiter = RateLimiter(max_requests=50, window_seconds=60.0)

        async def worker():
            return await limiter.acquire("concurrent-test")

        tasks = [worker() for _ in range(300)]
        results = await asyncio.gather(*tasks)
        allowed = sum(results)
        assert allowed == 50  # exactly max_requests
        assert len(results) - allowed == 250

    @pytest.mark.asyncio
    async def test_concurrent_mixed_ips(self):
        """Multiple IPs hitting limiter concurrently — each gets independent quota."""
        limiter = RateLimiter(max_requests=10, window_seconds=60.0)

        async def worker(ip):
            return await limiter.acquire(ip)

        tasks = []
        for ip_suffix in range(20):
            ip = f"192.168.0.{ip_suffix}"
            for _ in range(15):
                tasks.append(worker(ip))

        results = await asyncio.gather(*tasks)
        allowed = sum(results)
        assert allowed == 200  # 20 IPs x 10 each
        assert len(results) - allowed == 100  # 5 rejected per IP


# ── TTLCache ─────────────────────────────────────────────

class TestTTLCache:
    """In-memory TTL cache."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        cache = TTLCache(ttl=30.0)
        await cache.set("key1", "value1")
        assert await cache.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_miss_returns_none(self):
        cache = TTLCache(ttl=30.0)
        assert await cache.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_expiry(self):
        cache = TTLCache(ttl=0.2)
        await cache.set("k", "v")
        assert await cache.get("k") == "v"
        await asyncio.sleep(0.25)
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_maxsize_eviction(self):
        cache = TTLCache(maxsize=3, ttl=60.0)
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)
        await cache.set("d", 4)  # evicts oldest (a)
        assert await cache.get("a") is None
        assert await cache.get("b") == 2
        assert await cache.get("c") == 3
        assert await cache.get("d") == 4

    @pytest.mark.asyncio
    async def test_delete(self):
        cache = TTLCache(ttl=60.0)
        await cache.set("x", 100)
        await cache.delete("x")
        assert await cache.get("x") is None

    @pytest.mark.asyncio
    async def test_concurrent_reads(self):
        """100 concurrent reads should not corrupt cache."""
        cache = TTLCache(ttl=60.0)
        await cache.set("shared", 42)

        async def reader():
            val = await cache.get("shared")
            return val == 42

        tasks = [reader() for _ in range(100)]
        results = await asyncio.gather(*tasks)
        assert all(results)

    @pytest.mark.asyncio
    async def test_concurrent_writes_same_key(self):
        """Concurrent writes to same key — last write wins, no corruption."""
        cache = TTLCache(ttl=60.0)

        async def writer(val):
            await cache.set("race", val)

        tasks = [writer(i) for i in range(100)]
        await asyncio.gather(*tasks)
        result = await cache.get("race")
        assert result is not None
        assert 0 <= result <= 99

    @pytest.mark.asyncio
    async def test_does_not_hold_expired_entries(self):
        """Expired entries are cleaned up and don't consume memory."""
        cache = TTLCache(ttl=0.2, maxsize=2)
        await cache.set("a", 1)
        await cache.set("b", 2)
        await asyncio.sleep(0.25)
        # Both expired — cache should be empty
        assert await cache.get("a") is None
        assert await cache.get("b") is None
        # New entries should work
        await cache.set("c", 3)
        assert await cache.get("c") == 3
