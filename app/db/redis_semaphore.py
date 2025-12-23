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
from typing import Optional

import redis.asyncio as aioredis

from app import config
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
        redis = await self._get_redis()
        token = str(uuid.uuid4())
        start_time = time.monotonic()
        retry_delay = 0.5  # Start with 500ms delay
        max_retry_delay = 5.0  # Cap at 5 seconds
        
        while True:
            # Clean up expired slots first
            now = time.time()
            await redis.zremrangebyscore(self._key, "-inf", now)
            
            # Check current count
            current_count = await redis.zcard(self._key)
            
            if current_count < self.max_concurrent:
                # Try to add our slot atomically
                expire_at = now + self.slot_ttl
                added = await redis.zadd(
                    self._key,
                    {token: expire_at},
                    nx=True,  # Only add if doesn't exist
                )
                if added:
                    logger.debug(
                        f"Acquired distributed GIS slot: {token[:8]}... "
                        f"({current_count + 1}/{self.max_concurrent} slots in use)"
                    )
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
async def distributed_gis_slot(timeout: Optional[float] = None):
    """
    Acquire a distributed slot for a GIS operation.
    Coordinates across all containers/processes using Redis.
    
    Args:
        timeout: Maximum time to wait for a slot in seconds.
                 Defaults to gis_operation_timeout config setting (1 hour).
    
    Usage:
        async with distributed_gis_slot():
            # Your GIS operation here
            result = await fetch_rasters_for_regions(...)
    """
    if timeout is None:
        timeout = global_settings.gis_operation_timeout
    
    semaphore = get_gis_distributed_semaphore()
    token = await semaphore.acquire(timeout=timeout)
    try:
        yield
    finally:
        await semaphore.release(token)


def reset_gis_distributed_semaphore():
    """Reset the distributed semaphore (useful for testing)."""
    global _gis_distributed_semaphore
    _gis_distributed_semaphore = None
