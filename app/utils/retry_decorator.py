import asyncio
from functools import wraps

def retry_async(
    retries: int = 3,
    exceptions: tuple = (Exception,),
    delay: int = 1,
):
    """Decorator to retry asynchronous functions.

    Args:
        retries: Number of retries if the function fails.
        exceptions: Exceptions that trigger a retry.
        delay: Delay between retries.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for _ in range(retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if _ == retries - 1:  # On the last retry, raise the exception
                        raise e
                    await asyncio.sleep(delay)  # Wait for some time before retrying
        return wrapper
    return decorator