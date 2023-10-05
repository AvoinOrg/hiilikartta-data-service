from enum import Enum

class CalculationStatus(Enum):
    STARTED = "started"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
