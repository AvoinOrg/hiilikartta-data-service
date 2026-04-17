from rich.logging import RichHandler

from app.utils.logger import get_logger


def test_get_logger_avoids_duplicate_rich_handlers():
    logger_name = "tests.modules.test_logger.unique"

    logger = get_logger(logger_name)
    same_logger = get_logger(logger_name)

    rich_handlers = [
        handler for handler in logger.handlers if isinstance(handler, RichHandler)
    ]

    assert logger is same_logger
    assert len(rich_handlers) == 1
    assert logger.propagate is False
