"""
Distributed (cross-process/cross-container) semaphore for GIS operations.

This module provides a Redis-based semaphore that coordinates concurrent
GIS database operations across multiple processes, workers, and containers.
This is essential when you have multiple SAQ workers or FastAPI instances
that all need to share a limited number of database connections.
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import Optional

import redis.asyncio as aioredis

from app import config
from app.db.errors import GisRetryLaterError
from app.utils.logger import get_logger

logger = get_logger(__name__)
global_settings = config.get_settings()


class RedisDistributedSemaphore:
    """
    A distributed semaphore using Redis to coordinate across multiple
    processes/containers.
    
    Uses a Redis sorted set where:
    - Members are unique tokens identifying each slot holder
    - Scores are expiration timestamps (for automatic cleanup of stuck slots)
    """

    _ACQUIRE_SCRIPT = """
    local key = KEYS[1]
    local max_concurrent = tonumber(ARGV[1])
    local token = ARGV[2]
    local expire_at = tonumber(ARGV[3])
    local now = tonumber(ARGV[4])

    redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
    local current_count = redis.call('ZCARD', key)
    if current_count >= max_concurrent then
        return {0, current_count}
    end

    local added = redis.call('ZADD', key, 'NX', expire_at, token)
    if added == 1 then
        return {1, current_count + 1}
    end

    return {0, current_count}
    """

    def __init__(
        self,
        redis_url: str,
        name: str,
        max_concurrent: int,
        slot_ttl: int = 3600,  # 1 hour default TTL for stuck slots
    ):
        """
        Initialize the distributed semaphore.
        
        Args:
            redis_url: Redis connection URL
            name: Unique name for this semaphore (used as Redis key prefix)
            max_concurrent: Maximum number of concurrent operations allowed
            slot_ttl: Time-to-live in seconds for each slot (prevents deadlocks
                      if a process crashes without releasing its slot)
        """
        self.redis_url = redis_url
        self.name = name
        self.max_concurrent = max_concurrent
        self.slot_ttl = slot_ttl
        self._redis: Optional[aioredis.Redis] = None
    
    async def _get_redis(self) -> aioredis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = aioredis.from_url(self.redis_url)
        return self._redis
    
    @property
    def _key(self) -> str:
        """Redis key for the semaphore's sorted set."""
        return f"gis_semaphore:{self.name}"

    async def try_acquire(self) -> tuple[Optional[str], int]:
        """
        Attempt to acquire a slot once.

        Returns:
            A tuple of (token, current_count). token is None if not acquired.
        """
        redis = await self._get_redis()
        token = str(uuid.uuid4())
        now = time.time()
        expire_at = now + self.slot_ttl

        try:
            result = await redis.eval(
                self._ACQUIRE_SCRIPT,
                1,
                self._key,
                self.max_concurrent,
                token,
                expire_at,
                now,
            )
        except Exception:
            logger.exception("Failed to acquire distributed GIS slot (Redis error)")
            raise

        acquired = bool(result and int(result[0]) == 1)
        current_count = int(result[1]) if result and len(result) > 1 else 0

        if acquired:
            logger.debug(
                f"Acquired distributed GIS slot: {token[:8]}... "
                f"({current_count}/{self.max_concurrent} slots in use)"
            )
            return token, current_count

        return None, current_count

    async def _refresh_lease_loop(self, token: str, interval_seconds: float) -> None:
        redis = await self._get_redis()
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                expire_at = time.time() + self.slot_ttl
                await redis.zadd(self._key, {token: expire_at}, xx=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Distributed GIS slot lease refresh failed")

    async def acquire(self, timeout: Optional[float] = None) -> str:
        """
        Acquire a slot. Returns a token that must be used to release.
        
        Args:
            timeout: Maximum time to wait for a slot (seconds). None = wait forever.
        
        Returns:
            A token string to use when releasing the slot.
        
        Raises:
            asyncio.TimeoutError: If timeout exceeded.
        """
        start_time = time.monotonic()
        retry_delay = 0.5  # Start with 500ms delay
        max_retry_delay = 5.0  # Cap at 5 seconds
        
        while True:
            token, current_count = await self.try_acquire()
            if token is not None:
                return token
            
            # Check timeout
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    logger.warning(
                        f"Timed out waiting for distributed GIS slot after {elapsed:.1f}s "
                        f"({current_count}/{self.max_concurrent} slots in use)"
                    )
                    raise asyncio.TimeoutError(
                        f"Timed out waiting for distributed semaphore slot after {elapsed:.1f}s"
                    )
            
            # Log waiting status periodically
            if int(time.monotonic() - start_time) % 30 == 0 and time.monotonic() - start_time > 1:
                logger.info(
                    f"Waiting for distributed GIS slot... "
                    f"({current_count}/{self.max_concurrent} slots in use)"
                )
            
            # Wait before retrying with exponential backoff (capped)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, max_retry_delay)
    
    async def release(self, token: str) -> None:
        """Release a slot using the token from acquire()."""
        redis = await self._get_redis()
        removed = await redis.zrem(self._key, token)
        if removed:
            current_count = await redis.zcard(self._key)
            logger.debug(
                f"Released distributed GIS slot: {token[:8]}... "
                f"({current_count}/{self.max_concurrent} slots now in use)"
            )
        else:
            logger.warning(f"GIS slot already released or expired: {token[:8]}...")
    
    async def get_current_count(self) -> int:
        """Get the current number of slots in use."""
        redis = await self._get_redis()
        # Clean up expired first
        now = time.time()
        await redis.zremrangebyscore(self._key, "-inf", now)
        return await redis.zcard(self._key)
    
    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None


# Global instance for GIS operations
_gis_distributed_semaphore: Optional[RedisDistributedSemaphore] = None


def get_gis_distributed_semaphore() -> RedisDistributedSemaphore:
    """
    Get or create the distributed GIS semaphore.
    
    Configuration via config settings:
    - gis_distributed_max_concurrent: Max concurrent operations (default: 5)
    - gis_slot_ttl: Time-to-live for slots in seconds (default: 7200 = 2 hours)
    - redis_url: Redis connection URL
    """
    global _gis_distributed_semaphore
    if _gis_distributed_semaphore is None:
        max_concurrent = global_settings.gis_distributed_max_concurrent
        slot_ttl = global_settings.gis_slot_ttl
        
        _gis_distributed_semaphore = RedisDistributedSemaphore(
            redis_url=global_settings.redis_url,
            name="gis_operations",
            max_concurrent=max_concurrent,
            slot_ttl=slot_ttl,
        )
        logger.info(
            f"Created distributed GIS semaphore with max_concurrent={max_concurrent}, "
            f"slot_ttl={slot_ttl}s"
        )
    return _gis_distributed_semaphore


@asynccontextmanager
async def distributed_gis_slot(retry_in_seconds: float = 60.0):
    """
    Acquire a distributed slot for a GIS operation.
    Coordinates across all containers/processes using Redis.
    
    Args:
        retry_in_seconds: How long the worker should wait before retrying
                          when no slot is available.
    
    Usage:
        async with distributed_gis_slot():
            # Your GIS operation here
            result = await fetch_rasters_for_regions(...)
    """
    semaphore = get_gis_distributed_semaphore()
    token, _ = await semaphore.try_acquire()
    if token is None:
        raise GisRetryLaterError(
            "No distributed GIS semaphore slots available",
            retry_in_seconds=retry_in_seconds,
        )

    heartbeat_interval = min(60.0, max(1.0, semaphore.slot_ttl / 3))
    lease_task = asyncio.create_task(semaphore._refresh_lease_loop(token, heartbeat_interval))
    try:
        yield
    finally:
        lease_task.cancel()
        with suppress(asyncio.CancelledError):
            await lease_task
        await semaphore.release(token)


def reset_gis_distributed_semaphore():
    """Reset the distributed semaphore (useful for testing)."""
    global _gis_distributed_semaphore
    _gis_distributed_semaphore = None
