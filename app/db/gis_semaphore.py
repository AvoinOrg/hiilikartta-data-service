"""
Local (per-process) semaphore for limiting concurrent GIS database operations.

This module provides a simple asyncio-based semaphore that limits how many
GIS operations can run concurrently within a single process/worker.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Global semaphore - limits concurrent GIS operations within this process
_gis_semaphore: Optional[asyncio.Semaphore] = None


def get_gis_semaphore() -> asyncio.Semaphore:
    """
    Get or create the GIS operation semaphore.
    
    The max concurrent operations can be configured via GIS_LOCAL_MAX_CONCURRENT
    environment variable (default: 3).
    """
    global _gis_semaphore
    if _gis_semaphore is None:
        settings = get_settings()
        max_concurrent = settings.gis_local_max_concurrent
        _gis_semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"Created GIS local semaphore with max_concurrent={max_concurrent}")
    return _gis_semaphore


@asynccontextmanager
async def gis_operation_slot(timeout: Optional[float] = None):
    """
    Acquire a slot for a GIS operation. Use this to limit concurrent
    heavy GIS queries within this process.
    
    Args:
        timeout: Optional timeout in seconds. If None, waits indefinitely.
    
    Raises:
        asyncio.TimeoutError: If timeout is specified and exceeded.
    
    Usage:
        async with gis_operation_slot():
            # Your GIS operation here
            result = await fetch_rasters_for_regions(...)
    """
    semaphore = get_gis_semaphore()
    
    if timeout is not None and timeout <= 0:
        # Fail-fast path: avoid awaiting, so we don't occupy a worker slot while waiting.
        # asyncio.Semaphore has no public try_acquire; `_value` is safe to read/write
        # atomically within the event loop.
        current_value = getattr(semaphore, "_value", 0)
        if current_value <= 0:
            logger.debug("No GIS local operation slot available (fail-fast)")
            raise asyncio.TimeoutError()

        semaphore._value = current_value - 1  # type: ignore[attr-defined]
    elif timeout is not None:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Timed out waiting for GIS operation slot after {timeout}s")
            raise
    else:
        await semaphore.acquire()
    
    try:
        logger.debug("Acquired GIS local operation slot")
        yield
    finally:
        semaphore.release()
        logger.debug("Released GIS local operation slot")


def reset_gis_semaphore():
    """Reset the semaphore (useful for testing)."""
    global _gis_semaphore
    _gis_semaphore = None
