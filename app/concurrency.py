"""
Concurrency utilities: rate limiting and in-memory caching.

All state is per-worker (process). With uvicorn --workers, each worker has
its own independent rate limiter and cache. This is acceptable for most
use cases without needing Redis.
"""

import asyncio
import hashlib
import time
from collections import defaultdict
from functools import wraps
from typing import Callable, Awaitable

from fastapi import Request, HTTPException, status


# ── Sliding-window per-IP rate limiter ──

class RateLimiter:
    """Sliding window rate limiter using in-memory counters per IP."""

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def _clean_bucket(self, key: str, now: float):
        cutoff = now - self.window
        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    async def acquire(self, key: str) -> bool:
        """Try to acquire a token. Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        async with self._lock:
            await self._clean_bucket(key, now)
            if len(self._buckets[key]) >= self.max_requests:
                return False
            self._buckets[key].append(now)
            return True

    async def cleanup(self):
        """Remove expired entries to prevent memory leak."""
        async with self._lock:
            now = time.monotonic()
            expired = [k for k in self._buckets if not self._buckets[k]]
            for k in expired:
                del self._buckets[k]


# ── Simple TTL cache ──

class TTLCache:
    """In-memory TTL cache with max size eviction."""

    def __init__(self, maxsize: int = 512, ttl: float = 30.0):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> object | None:
        now = time.monotonic()
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expiry, value = entry
            if now > expiry:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: object):
        async with self._lock:
            if len(self._store) >= self.maxsize:
                # Evict oldest entry
                oldest = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest]
            self._store[key] = (time.monotonic() + self.ttl, value)

    async def delete(self, key: str):
        async with self._lock:
            self._store.pop(key, None)


# ── Async cache decorator ──

def cached(ttl: float = 30.0, key_fn: Callable = None):
    """Decorator for caching async function results in TTLCache."""

    def decorator(func: Callable[..., Awaitable]):
        cache = TTLCache(ttl=ttl)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = key_fn(*args, **kwargs) if key_fn else _default_key(args, kwargs)
            cached_val = await cache.get(cache_key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            await cache.set(cache_key, result)
            return result

        wrapper.cache = cache
        return wrapper

    return decorator


def _default_key(args, kwargs) -> str:
    raw = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(raw.encode()).hexdigest()
