from enum import Enum

class CalculationStatus(Enum):
    PROCESSING = "processing"
    FINISHED = "finished"
    ERROR = "error"
