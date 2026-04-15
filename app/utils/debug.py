"""
Debug utilities for saving uploaded files for inspection.
"""

import shutil
from datetime import datetime
from pathlib import Path
from fastapi import UploadFile

from app.utils.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path("/app/data")


def save_upload_for_debug(file: UploadFile, prefix: str = "calculation-endpoint-data") -> Path:
    """
    Save an uploaded file to /app/data for debugging purposes.
    
    Args:
        file: The uploaded file from FastAPI
        prefix: Prefix for the filename
    
    Returns:
        Path to the saved file
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Get original extension if available
    original_name = file.filename or ""
    extension = Path(original_name).suffix or ".zip"
    
    filename = f"{prefix}-{timestamp}{extension}"
    filepath = DATA_DIR / filename
    
    # Save current position
    current_pos = file.file.tell()
    file.file.seek(0)
    
    with open(filepath, "wb") as dest:
        shutil.copyfileobj(file.file, dest)
    
    # Reset file position so it can still be used
    file.file.seek(current_pos)
    
    logger.info(f"Debug: Saved upload to {filepath}")
    return filepath
