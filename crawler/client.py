"""Async HTTP client with token-bucket rate limiting and retry logic."""

import asyncio
import logging
import random
import time
from typing import Any, Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.refill_rate
                await asyncio.sleep(wait)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0


class MoltbookClient:
    """Async API client for Moltbook with rate limiting and retries."""

    def __init__(self, config: Config):
        self.config = config
        self.bucket = TokenBucket(config.bucket_capacity, config.bucket_refill_rate)
        self.semaphore = asyncio.Semaphore(config.max_concurrent)
        self.session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._error_count = 0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": self.config.user_agent},
        )
        return self

    async def __aexit__(self, *exc):
        if self.session:
            await self.session.close()
            self.session = None

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def error_count(self) -> int:
        return self._error_count

    async def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request with rate limiting and retries."""
        url = f"{self.config.base_url}{path}"

        for attempt in range(self.config.max_retries + 1):
            async with self.semaphore:
                await self.bucket.acquire()
                self._request_count += 1

                try:
                    async with self.session.get(url, params=params) as resp:
                        if resp.status == 200:
                            return await resp.json()

                        if resp.status == 429:
                            retry_after = resp.headers.get("Retry-After")
                            wait = float(retry_after) if retry_after else self.config.default_429_wait
                            logger.warning("Rate limited (429), waiting %.1fs", wait)
                            await asyncio.sleep(wait)
                            continue

                        if resp.status >= 500:
                            if attempt < self.config.max_retries:
                                delay = self.config.retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                                logger.warning("Server error %d, retry %d in %.1fs", resp.status, attempt + 1, delay)
                                await asyncio.sleep(delay)
                                continue
                            self._error_count += 1
                            resp.raise_for_status()

                        # 4xx (other than 429)
                        self._error_count += 1
                        text = await resp.text()
                        logger.error("Client error %d: %s", resp.status, text[:200])
                        return {"success": False, "error": text, "status": resp.status}

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt < self.config.max_retries:
                        delay = self.config.retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning("Network error: %s, retry %d in %.1fs", e, attempt + 1, delay)
                        await asyncio.sleep(delay)
                        continue
                    self._error_count += 1
                    raise

        # Should not reach here
        self._error_count += 1
        raise RuntimeError(f"Exhausted retries for {path}")

    async def get_submolts(self, offset: int = 0, limit: int = 50) -> dict:
        return await self.get("/submolts", {"limit": limit, "offset": offset})

    async def get_posts(self, sort: str = "new", offset: int = 0, limit: int = 50) -> dict:
        return await self.get("/posts", {"sort": sort, "limit": limit, "offset": offset})

    async def get_post_comments(self, post_id: str) -> dict:
        return await self.get(f"/posts/{post_id}/comments")
