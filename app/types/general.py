from enum import Enum


class CalculationStatus(Enum):
    PROCESSING = "PROCESSING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    NOT_STARTED = "NOT_STARTED"
